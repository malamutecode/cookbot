"""Unit tests for the lexical matcher — hermetic, no network."""

from __future__ import annotations

import pytest

from delivery_shops.matcher import ProductMatcher
from delivery_shops.models import Product
from delivery_shops.shops.frisco import parse_feed


def _p(id: str, name: str, keywords: str = "", **kw) -> Product:
    return Product(id=id, name=name, keywords=keywords, url=f"https://shop/{id}", **kw)


@pytest.fixture
def products() -> list[Product]:
    return [
        _p("1", "Cebula obrana mix 4 szt.", "cebula warzywa", price=4.79),
        _p("2", "Mąka pszenna tradycyjna (typ 550)", "maka pieczenie", price=2.99),
        _p("3", "Masło ekstra 82%", "maslo nabial", price=7.49),
        _p("4", "Papryka czerwona luz", "papryka warzywa", price=12.90),
        # Deliberately no price — locks the missing-price regression.
        _p("5", "Czosnek świeży", "czosnek warzywa", price=None),
        # Out of stock — should be beaten by an available match on a tie.
        _p("6", "Cebula czerwona BIO", "cebula warzywa", price=5.99, available=False),
    ]


def test_matches_obvious_ingredients(products: list[Product]) -> None:
    m = ProductMatcher(products)
    assert m.match("cebula", "frisco").product.id == "1"
    assert m.match("mąka pszenna", "frisco").product.id == "2"
    assert m.match("masło", "frisco").product.id == "3"
    assert m.match("papryka czerwona", "frisco").product.id == "4"


def test_missing_price_product_still_matches(products: list[Product]) -> None:
    match = ProductMatcher(products).match("czosnek", "frisco")
    assert match is not None
    assert match.product.id == "5"
    assert match.product.price is None


def test_availability_breaks_ties(products: list[Product]) -> None:
    # Both cebula products share tokens; the available one (id 1) must win.
    assert ProductMatcher(products).match("cebula", "frisco").product.id == "1"


def test_no_match_returns_none(products: list[Product]) -> None:
    assert ProductMatcher(products).match("kawior z bieługi", "frisco") is None
    assert ProductMatcher(products).match("", "frisco") is None


def test_match_all_splits_matched_and_unmatched(products: list[Product]) -> None:
    matched, unmatched = ProductMatcher(products).match_all(
        ["cebula", "kawior z bieługi", "masło"], "frisco"
    )
    assert [x.product.id for x in matched] == ["1", "3"]
    assert [x.ingredient for x in unmatched] == ["kawior z bieługi"]


def test_diacritic_insensitive(products: list[Product]) -> None:
    # Query without diacritics still matches a product whose name has them.
    assert ProductMatcher(products).match("czosnek swiezy", "frisco").product.id == "5"


def test_parse_feed_filters_unavailable_and_maps_fields() -> None:
    payload = {
        "generatedAt": "2026-07-04T00:00:00+02:00",
        "products": [
            {
                "id": "41429",
                "name": {"pl": "Pasta curry"},
                "keywords": {"pl": "azjatycka"},
                "brand": "KANOKWAN",
                "grammage": 0.05,
                "unitOfMeasure": "Kilogram",
                "price": {"price": 5.89},
                "isAvailable": True,
                "productUrl": "https://www.frisco.pl/pid,41429/",
                "imageUrl": "https://img/41429.png",
            },
            {  # unavailable → dropped
                "id": "2",
                "name": {"pl": "Coś"},
                "isAvailable": False,
                "productUrl": "https://www.frisco.pl/pid,2/",
            },
            {  # no url → dropped
                "id": "3",
                "name": {"pl": "Bez url"},
                "isAvailable": True,
            },
        ],
    }
    products, generated_at = parse_feed(payload)
    assert generated_at == "2026-07-04T00:00:00+02:00"
    assert [p.id for p in products] == ["41429"]
    p = products[0]
    assert p.name == "Pasta curry"
    assert p.brand == "KANOKWAN"
    assert p.price == 5.89
    assert p.url == "https://www.frisco.pl/pid,41429/"
