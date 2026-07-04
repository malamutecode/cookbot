"""Frisco.pl delivery shop — the only Frisco-aware code in the package.

Fetches Frisco's public product feed (~50 MB JSON, ~14.7k products, regenerated
daily), maps its schema onto the generic :class:`Product`, and caches the parsed
result in memory with a TTL. All I/O is async; concurrent loads are serialised
behind a lock so the feed is downloaded once, not per request.

Feed shape (verified 2026-07-04): ``{generatedAt, categories[], products[]}``.
Each product: ``id``, ``name.pl``, ``keywords.pl``, ``brand``, ``grammage``,
``unitOfMeasure``, ``price.price`` (may be absent), ``isAvailable``, ``stock``,
``productUrl``, ``imageUrl``.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
import structlog

from delivery_shops.models import Product

log = structlog.get_logger()

_DEFAULT_FEED_URL = "https://commerce.frisco.pl/api/v1/integration/feeds/public?language=pl"
_DEFAULT_TTL_SECONDS = 12 * 60 * 60  # feed regenerates daily; 12h is safe headroom
_FETCH_TIMEOUT_SECONDS = 120.0


def _lang(value: Any) -> str:
    """Pull Polish text from a ``{"pl": ..., "en": ...}`` field, tolerating a
    plain string or a missing/oddly-shaped value."""
    if isinstance(value, dict):
        return str(value.get("pl") or value.get("en") or "")
    if isinstance(value, str):
        return value
    return ""


def _to_product(raw: dict[str, Any]) -> Product | None:
    """Map one raw Frisco feed entry to a generic ``Product`` (best-effort)."""
    product_id = raw.get("id") or raw.get("productId")
    url = raw.get("productUrl")
    if not product_id or not url:
        return None

    price_obj = raw.get("price")
    price = price_obj.get("price") if isinstance(price_obj, dict) else None

    primary_category = raw.get("primaryCategory")
    category = _lang(primary_category.get("name")) if isinstance(primary_category, dict) else ""

    return Product(
        id=str(product_id),
        name=_lang(raw.get("name")),
        keywords=_lang(raw.get("keywords")),
        category=category,
        brand=raw.get("brand") or raw.get("producer"),
        price=price,
        grammage=raw.get("grammage"),
        unit=raw.get("unitOfMeasure"),
        url=str(url),
        image_url=raw.get("imageUrl"),
        available=bool(raw.get("isAvailable", True)),
    )


def parse_feed(payload: dict[str, Any]) -> tuple[list[Product], str | None]:
    """Parse a raw Frisco feed dict into available-only ``Product``s + timestamp.

    Pure and side-effect-free so it can be unit-tested without a network call.
    """
    generated_at = payload.get("generatedAt")
    products: list[Product] = []
    for raw in payload.get("products", []):
        if not isinstance(raw, dict):
            continue
        product = _to_product(raw)
        if product is not None and product.available:
            products.append(product)
    return products, generated_at


class FriscoShop:
    """A ``DeliveryShop`` backed by Frisco's public feed, with a TTL cache."""

    shop_id = "frisco"

    def __init__(
        self,
        feed_url: str | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        self._feed_url = feed_url or os.environ.get("FRISCO_FEED_URL", _DEFAULT_FEED_URL)
        self._ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else float(os.environ.get("FRISCO_FEED_TTL_SECONDS", _DEFAULT_TTL_SECONDS))
        )
        self._lock = asyncio.Lock()
        self._cache: tuple[list[Product], str | None] | None = None
        self._fetched_at = 0.0

    def _is_fresh(self) -> bool:
        return self._cache is not None and (time.monotonic() - self._fetched_at) < self._ttl

    async def load(self) -> tuple[list[Product], str | None]:
        if self._is_fresh():
            assert self._cache is not None
            return self._cache
        async with self._lock:
            # Re-check: another coroutine may have refreshed while we waited.
            if self._is_fresh():
                assert self._cache is not None
                return self._cache
            log.info("frisco_feed_fetch_start", url=self._feed_url)
            async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_SECONDS) as client:
                resp = await client.get(self._feed_url)
                resp.raise_for_status()
                payload = resp.json()
            products, generated_at = parse_feed(payload)
            self._cache = (products, generated_at)
            self._fetched_at = time.monotonic()
            log.info(
                "frisco_feed_loaded",
                products=len(products),
                generated_at=generated_at,
            )
            return self._cache
