"""Frisco.pl delivery shop — the only Frisco-aware code in the package.

Two ways to resolve an ingredient, in preference order:

**1. Live search (preferred, STEP 50).** ``GET /api/v1/offer/products/query``
answers unauthenticated in ~120-150 ms and ranks results with Frisco's own
relevance engine. A whole shopping list fans out through :meth:`search_many`
behind a bounded semaphore over one pooled connection — 12 ingredients in ~585 ms
measured. Nothing is downloaded or indexed locally, so prices and stock are live.

**2. Feed dump (fallback).** ``load()`` fetches the ~50 MB public feed (~14.7k
products, regenerated daily), maps it onto :class:`Product`, and caches the parsed
result in memory with a TTL. Kept as the resilience path for when the search API
is unreachable; concurrent loads are serialised behind a lock.

Schema notes (both verified live 2026-07-25):

* Feed: ``{generatedAt, categories[], products[]}``; each product has ``id``,
  ``name.pl``, ``keywords.pl``, ``brand``, ``grammage``, ``unitOfMeasure``,
  ``price.price`` (may be absent), ``isAvailable``, ``productUrl``, ``imageUrl``.
* Search: same product shape **minus ``productUrl`` and ``keywords``**, plus
  ``primaryCategory``. The product URL is therefore reconstructed from the id —
  the slugless ``pid,{id}/stn,product`` form resolves (HTTP 200), so no SEO slug
  is needed. ``pageIndex`` is **1-based**; ``pageIndex=0`` is an HTTP 400.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx
import structlog

from delivery_shops.models import Product, ProductMatch

log = structlog.get_logger()

_DEFAULT_FEED_URL = "https://commerce.frisco.pl/api/v1/integration/feeds/public?language=pl"
_DEFAULT_TTL_SECONDS = 12 * 60 * 60  # feed regenerates daily; 12h is safe headroom
_FETCH_TIMEOUT_SECONDS = 120.0

_DEFAULT_SEARCH_URL = "https://commerce.frisco.pl/api/v1/offer/products/query"
# Frisco showed no throttling at 28 concurrent queries, but we have no agreement
# and no documented rate limit — a 40-item list must not open 40 sockets.
_DEFAULT_SEARCH_CONCURRENCY = 8
_DEFAULT_SEARCH_TIMEOUT_SECONDS = 10.0
# Product pages resolve without the SEO slug; verified 200 on the bare id form.
_PRODUCT_URL_TEMPLATE = "https://www.frisco.pl/pid,{id}/stn,product"


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


def _search_category(raw: dict[str, Any]) -> str:
    """Build the category text for a search hit.

    ``primaryCategory`` is the *deepest leaf* ("Malinowe" for raspberry tomatoes,
    "Jodowana" for iodised salt), which on its own loses the word a human would
    search for. The ``categories`` array carries the whole ancestry
    (``Warzywa i owoce > Pomidory > Duże > Malinowe``), so join that instead —
    it keeps the semantic anchor without discarding the specific leaf.

    Falls back to the leaf alone when the chain is missing.
    """
    chain = raw.get("categories")
    if isinstance(chain, list):
        names = [_lang(c.get("name")) for c in chain if isinstance(c, dict) and _lang(c.get("name"))]
        # Dedupe (a product can sit under the same name twice via two trees) while
        # preserving order.
        if names:
            return " ".join(dict.fromkeys(names))

    primary = raw.get("primaryCategory")
    return _lang(primary.get("name")) if isinstance(primary, dict) else ""


def _search_hit_to_product(raw: dict[str, Any]) -> Product | None:
    """Map one ``products[].product`` entry from a search response to ``Product``.

    Differs from :func:`_to_product` in two ways the API forces on us: there is no
    ``productUrl`` (rebuilt from the id) and no ``keywords`` (left empty — nothing
    on the search path indexes it, since Frisco does the ranking).
    """
    product_id = raw.get("id") or raw.get("productId")
    if not product_id:
        return None

    price_obj = raw.get("price")
    price = price_obj.get("price") if isinstance(price_obj, dict) else None

    category = _search_category(raw)

    return Product(
        id=str(product_id),
        name=_lang(raw.get("name")),
        keywords="",
        category=category,
        brand=raw.get("brand") or raw.get("producer"),
        price=price,
        grammage=raw.get("grammage"),
        unit=raw.get("unitOfMeasure"),
        url=_PRODUCT_URL_TEMPLATE.format(id=product_id),
        image_url=raw.get("imageUrl"),
        available=bool(raw.get("isAvailable", True)),
    )


def parse_search_response(payload: dict[str, Any], ingredient: str, shop: str, limit: int) -> list[ProductMatch]:
    """Parse a search response into ranked ``ProductMatch``es, best first.

    Pure and side-effect-free so it can be unit-tested without a network call.
    ``score`` carries Frisco's own ``ordererScore``; results arrive pre-ranked, so
    the payload order is preserved rather than re-sorted.
    """
    matches: list[ProductMatch] = []
    for hit in payload.get("products", []):
        if not isinstance(hit, dict):
            continue
        raw_product = hit.get("product")
        if not isinstance(raw_product, dict):
            continue
        product = _search_hit_to_product(raw_product)
        if product is None:
            continue
        score = hit.get("ordererScore")
        matches.append(
            ProductMatch(
                ingredient=ingredient,
                shop=shop,
                product=product,
                score=float(score) if isinstance(score, (int, float)) else 0.0,
            )
        )
        if len(matches) >= limit:
            break
    return matches


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
        search_url: str | None = None,
        search_concurrency: int | None = None,
        search_timeout_seconds: float | None = None,
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

        self._search_url = search_url or os.environ.get("FRISCO_SEARCH_URL", _DEFAULT_SEARCH_URL)
        self._search_timeout = (
            search_timeout_seconds
            if search_timeout_seconds is not None
            else float(os.environ.get("FRISCO_SEARCH_TIMEOUT_SECONDS", _DEFAULT_SEARCH_TIMEOUT_SECONDS))
        )
        concurrency = (
            search_concurrency
            if search_concurrency is not None
            else int(os.environ.get("FRISCO_SEARCH_CONCURRENCY", _DEFAULT_SEARCH_CONCURRENCY))
        )
        self._search_semaphore = asyncio.Semaphore(max(1, concurrency))
        # One pooled client shared across a fan-out — connection reuse is most of
        # why a 12-ingredient list resolves in well under a second. Created lazily
        # so constructing the shop never needs a running event loop.
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client
        async with self._client_lock:
            if self._client is None or self._client.is_closed:
                self._client = httpx.AsyncClient(timeout=self._search_timeout)
            return self._client

    async def aclose(self) -> None:
        """Release the pooled search client. Safe to call when never used."""
        client = self._client
        self._client = None
        if client is not None and not client.is_closed:
            await client.aclose()

    async def search(self, query: str, limit: int = 5) -> list[ProductMatch]:
        """Resolve one ingredient against Frisco's live search API."""
        if not query.strip():
            return []
        client = await self._get_client()
        async with self._search_semaphore:
            resp = await client.get(
                self._search_url,
                params={
                    "search": query,
                    "language": "pl",
                    "pageSize": limit,
                    "pageIndex": 1,  # 1-based; 0 is rejected with HTTP 400
                },
            )
        resp.raise_for_status()
        return parse_search_response(resp.json(), query, self.shop_id, limit)

    async def search_many(self, queries: list[str], limit: int = 5) -> dict[str, list[ProductMatch]]:
        """Resolve many ingredients concurrently, keyed by query.

        Per-query failures are contained: a query that errors maps to an empty
        list and the rest of the batch still returns. If *every* query fails the
        backend is presumed down and the error is re-raised, which is the caller's
        signal to fall back to the feed.
        """
        if not queries:
            return {}

        unique = list(dict.fromkeys(queries))  # dedupe, preserve first-seen order
        results = await asyncio.gather(*(self.search(q, limit) for q in unique), return_exceptions=True)

        out: dict[str, list[ProductMatch]] = {}
        failures: list[BaseException] = []
        for query, result in zip(unique, results):
            if isinstance(result, BaseException):
                failures.append(result)
                log.warning("frisco_search_failed", query=query, error=str(result))
                out[query] = []
            else:
                out[query] = result

        if failures and len(failures) == len(unique):
            log.error("frisco_search_all_failed", queries=len(unique))
            raise failures[0]
        return out

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
