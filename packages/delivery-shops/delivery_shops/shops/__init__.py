"""Registry of available delivery shops.

Add a shop = implement ``DeliveryShop`` in a module here and add one entry below.
"""

from __future__ import annotations

from delivery_shops.base import DeliveryShop
from delivery_shops.shops.frisco import FriscoShop

SHOPS: dict[str, DeliveryShop] = {
    "frisco": FriscoShop(),
}

__all__ = ["SHOPS", "FriscoShop"]
