"""delivery-shops — match shopping-list ingredients to real delivery-shop products.

A standalone, cookbot-independent library. Clients enable shops by id; the package
owns the shops, the matching engine, and the boundary models.
"""

from __future__ import annotations

from delivery_shops.base import DeliveryShop, get_shop
from delivery_shops.matcher import ProductMatcher, ReRanker
from delivery_shops.models import (
    GroceryMatchResult,
    Product,
    ProductMatch,
    UnmatchedItem,
)

__all__ = [
    "DeliveryShop",
    "get_shop",
    "ProductMatcher",
    "ReRanker",
    "Product",
    "ProductMatch",
    "UnmatchedItem",
    "GroceryMatchResult",
]
