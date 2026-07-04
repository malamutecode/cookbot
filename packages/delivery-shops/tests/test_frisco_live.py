"""Live integration test — downloads the real Frisco feed. Opt-in only.

Run with:  uv run pytest -m integration -v
Excluded from the default hermetic unit run.
"""

from __future__ import annotations

import pytest

from delivery_shops.matcher import ProductMatcher
from delivery_shops.shops.frisco import FriscoShop

pytestmark = pytest.mark.integration


async def test_frisco_load_and_match_live() -> None:
    products, generated_at = await FriscoShop().load()
    assert len(products) > 1000  # real feed has ~14k available products
    assert generated_at  # feed exposes a freshness stamp

    matcher = ProductMatcher(products)
    matched, unmatched = matcher.match_all(
        ["cebula", "mąka pszenna", "masło"], "frisco"
    )
    assert len(matched) >= 2
    for m in matched:
        assert m.product.url.startswith("https://www.frisco.pl/")


# Each query must land in a product whose category name contains one of these
# substrings (case/diacritic-insensitive). Category is the robust semantic anchor:
# it catches the real regressions (dishwasher salt is category "Sole", baby food
# "Deserki", etc.) without pinning an exact product that the feed may reorder.
_EXPECTED_CATEGORY_SUBSTR = {
    "sól": ["sol", "morsk", "jodowan", "kamienn", "himalaj"],  # NOT "Sole" (dishwasher)
    "szpinak": ["szpinak"],
    "makaron penne": ["penne", "makaron"],
    "parmezan": ["parmezan"],
    "śmietana 30%": ["30"],
    "czosnek": ["czosnek"],
    "cebula": ["cebul"],
    "ryż": ["ziarni", "ryz", "basmati", "jasmin"],
    "pomidory": ["pomidor"],
    "masło": ["maslo", "tradycyjn", "klarowan", "irlandzk", "duns"],
}


def _fold(s: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()


async def test_frisco_matching_quality_live() -> None:
    """Regression guard for the wrong-product bugs, measured on the real feed."""
    products, _ = await FriscoShop().load()
    matcher = ProductMatcher(products)

    failures: list[str] = []
    for query, expected in _EXPECTED_CATEGORY_SUBSTR.items():
        match = matcher.match(query, "frisco")
        if match is None:
            failures.append(f"{query!r}: unmatched")
            continue
        cat = _fold(match.product.category)
        if not any(sub in cat for sub in expected):
            failures.append(
                f"{query!r} -> {match.product.name!r} [cat={match.product.category!r}] "
                f"expected category containing one of {expected}"
            )
    assert not failures, "matching-quality regressions:\n" + "\n".join(failures)
