"""The pluggable shop interface + registry lookup.

A ``DeliveryShop`` knows how to load its own catalogue as generic ``Product``s.
Adding a shop = implement this protocol and register it in ``shops/__init__.py``.
Clients never implement shops — they only *enable* registered ones by id.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from delivery_shops.models import Product


@runtime_checkable
class DeliveryShop(Protocol):
    """A delivery shop that can yield its product catalogue for matching."""

    shop_id: str

    async def load(self) -> tuple[list[Product], str | None]:
        """Return ``(products, generated_at)``.

        ``generated_at`` is the catalogue's freshness stamp (or ``None`` if the
        shop doesn't expose one). Implementations should cache internally so
        repeated calls don't re-download the feed.
        """
        ...


def get_shop(shop_id: str) -> DeliveryShop:
    """Look up a registered shop by id, or raise ``ValueError`` if unknown."""
    from delivery_shops.shops import SHOPS

    try:
        return SHOPS[shop_id]
    except KeyError:
        raise ValueError(f"Unknown delivery shop: {shop_id!r}") from None
