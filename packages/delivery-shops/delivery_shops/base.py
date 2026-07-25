"""The pluggable shop interface + registry lookup.

A ``DeliveryShop`` knows how to load its own catalogue as generic ``Product``s.
Adding a shop = implement this protocol and register it in ``shops/__init__.py``.
Clients never implement shops — they only *enable* registered ones by id.

Two capabilities, one required:

* ``load()`` — **required**. A catalogue *dump*: every product, matched locally by
  :class:`~delivery_shops.matcher.ProductMatcher`.
* ``search()`` / ``search_many()`` — **optional**. A *query* contract for shops
  that expose their own search backend, which ranks better and stays fresh
  without downloading anything. Feed-only shops simply don't implement it.

Because ``search`` is optional, callers must **feature-detect** rather than assume
it — see :class:`SearchableShop` and :func:`supports_search`.
"""

from __future__ import annotations

from typing import Protocol, TypeGuard, runtime_checkable

from delivery_shops.models import Product, ProductMatch


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


@runtime_checkable
class SearchableShop(DeliveryShop, Protocol):
    """A shop that can answer ingredient queries against its own search backend.

    Implementations rank results themselves, so no local index is built and the
    ``ProductMatch.score`` carries the *shop's* relevance score — meaningful only
    within one shop's results, never compared across shops or against the lexical
    matcher's scale.
    """

    async def search(self, query: str, limit: int = 5) -> list[ProductMatch]:
        """Return up to ``limit`` ranked matches for one ingredient, best first.

        Returns an empty list when nothing matches. Implementations should let
        transport errors propagate so ``search_many`` can classify them.
        """
        ...

    async def search_many(self, queries: list[str], limit: int = 5) -> dict[str, list[ProductMatch]]:
        """Resolve many ingredients concurrently, keyed by query.

        A query that fails or matches nothing maps to an empty list — one bad
        ingredient never sinks the batch. Raises only if the backend is wholly
        unreachable, which is the caller's signal to fall back to ``load()``.
        """
        ...


def supports_search(shop: DeliveryShop) -> TypeGuard[SearchableShop]:
    """Whether ``shop`` implements the optional search capability."""
    return callable(getattr(shop, "search_many", None))


def get_shop(shop_id: str) -> DeliveryShop:
    """Look up a registered shop by id, or raise ``ValueError`` if unknown."""
    from delivery_shops.shops import SHOPS

    try:
        return SHOPS[shop_id]
    except KeyError:
        raise ValueError(f"Unknown delivery shop: {shop_id!r}") from None
