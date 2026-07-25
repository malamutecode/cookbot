"""Live integration test — hits Frisco's real search API. Opt-in only.

Run with:  uv run pytest -m integration tests/test_frisco_search_live.py -v
Excluded from the default hermetic unit run.

Unlike the feed test this makes no LLM calls and downloads nothing large — a
whole shopping list resolves in well under a second. Costs nothing but network.

> Frisco's Regulamin §8.4 requires prior written consent for commercial use.
> This test is dev-scale and read-only; see the STEP 50 blocker note in TASK.md
> before pointing production traffic at this API.
"""

from __future__ import annotations

import time
import unicodedata

import pytest

from delivery_shops.shops.frisco import FriscoShop

pytestmark = pytest.mark.integration


# Same semantic contract as the feed test: assert the *category* the match lands
# in, not an exact product, so Frisco reordering its catalogue can't fail the
# suite while a genuine wrong-product regression still does.
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
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()


async def test_frisco_search_matching_quality_live() -> None:
    """The API's own ranking must satisfy the same quality bar as the feed path."""
    shop = FriscoShop()
    try:
        results = await shop.search_many(list(_EXPECTED_CATEGORY_SUBSTR))
    finally:
        await shop.aclose()

    failures: list[str] = []
    for query, expected in _EXPECTED_CATEGORY_SUBSTR.items():
        matches = results.get(query, [])
        if not matches:
            failures.append(f"{query!r}: unmatched")
            continue
        top = matches[0].product
        if not any(sub in _fold(top.category) for sub in expected):
            failures.append(
                f"{query!r} -> {top.name!r} [cat={top.category!r}] expected category containing one of {expected}"
            )
    assert not failures, "search matching-quality regressions:\n" + "\n".join(failures)


async def test_search_returns_usable_product_urls_live() -> None:
    """URLs are reconstructed from the id — they must still resolve on frisco.pl."""
    shop = FriscoShop()
    try:
        matches = await shop.search("masło", limit=3)
    finally:
        await shop.aclose()

    assert matches, "expected at least one match for 'masło'"
    for m in matches:
        assert m.product.url.startswith("https://www.frisco.pl/pid,")
        assert m.product.name
        assert m.product.id


async def test_shopping_list_fan_out_is_fast_live() -> None:
    """The whole point of the fan-out: a full list in about one second."""
    ingredients = list(_EXPECTED_CATEGORY_SUBSTR)
    shop = FriscoShop()
    started = time.perf_counter()
    try:
        results = await shop.search_many(ingredients)
    finally:
        await shop.aclose()
    elapsed = time.perf_counter() - started

    assert len(results) == len(ingredients)
    # Measured ~0.6s for 12 queries at concurrency 8; 10s is a generous ceiling
    # that still fails loudly if the fan-out silently serialises.
    assert elapsed < 10.0, f"fan-out took {elapsed:.1f}s — is it running serially?"
