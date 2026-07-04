"""Generic, shop-neutral data models for delivery-shop product matching.

Every boundary object (what a shop yields, what the matcher returns, what a client
route responds with) is a Pydantic model so callers never juggle raw dicts.
"""

from __future__ import annotations

from pydantic import BaseModel


class Product(BaseModel):
    """A single purchasable product from a delivery shop, normalised across shops.

    `price`/`grammage`/`unit` are optional because real feeds omit them on some
    rows (verified on Frisco — some products carry no `price`).
    """

    id: str
    name: str
    keywords: str = ""
    category: str = ""
    brand: str | None = None
    price: float | None = None
    grammage: float | None = None
    unit: str | None = None
    url: str
    image_url: str | None = None
    available: bool = True


class ProductMatch(BaseModel):
    """One shopping-list ingredient resolved to its best-matching product."""

    ingredient: str
    shop: str
    product: Product
    score: float


class UnmatchedItem(BaseModel):
    """An ingredient the matcher could not confidently resolve to a product."""

    ingredient: str


class GroceryMatchResult(BaseModel):
    """The full result of matching a shopping list against one shop's catalogue."""

    shop: str
    matched: list[ProductMatch]
    unmatched: list[UnmatchedItem]
    generated_at: str | None = None
