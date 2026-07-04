"""Matching-quality regression tests.

These lock in the fixes for the wrong-product bugs found against the live Frisco
feed: ``sól`` -> dishwasher salt, ``szpinak`` -> baby food, ``makaron`` -> sausage,
``pierś z kurczaka`` -> liver, ``parmezan`` -> not found. Each case models the real
competing products (name + category + keywords) so the scoring can't silently
regress. Hermetic — no network.
"""

from __future__ import annotations

import pytest

from delivery_shops.matcher import ProductMatcher
from delivery_shops.models import Product


def _p(id: str, name: str, category: str = "", keywords: str = "", **kw) -> Product:
    return Product(
        id=id, name=name, category=category, keywords=keywords, url=f"https://frisco/{id}", **kw
    )


@pytest.fixture
def catalogue() -> list[Product]:
    return [
        # sól: cooking salt must beat dishwasher salt (which only shares "sól" in
        # the name but sits in a non-food category and carries noise tokens).
        _p("salt-food", "Sól morska drobnoziarnista", "Morska", "sol przyprawa"),
        _p("salt-dish", "Sól ochronna do zmywarki 4000g", "Sole", "zmywarka do naczyn"),
        # szpinak: fresh spinach must beat freeze-dried colorant powder and baby food.
        _p("spinach", "Szpinak liście", "Szpinak", "warzywa mrozone"),
        _p("spinach-powder", "Szpinak liofilizowany proszek", "Barwniki i aromaty", "barwnik"),
        _p("babyfood", "Mus szpinak z jabłkiem po 6 miesiącu", "Deserki dla dzieci", "dziecko"),
        # makaron: pasta must not match sausage that mentions pasta in keywords.
        _p("pasta", "Makaron penne", "Penne", "makaron wloski"),
        _p("sausage", "Kiełbasa śląska", "Kiełbasy", "do makaronu obiad kolacja"),
        # parmezan: real product is named "Parmigiano" but category is "Parmezan".
        _p("parmesan", "Ser Parmigiano Reggiano", "Parmezan", "wloski ser twardy"),
        # pierś z kurczaka: fillet must beat chicken liver (both share "kurczaka").
        _p("breast", "Filet z piersi kurczaka", "Filet", "drobiowe swieze"),
        _p("liver", "Wątróbka z kurczaka świeża", "Podroby", "drobiowe swieze"),
        # śmietana 30%: exact-fat product must win over 12% and 18%.
        _p("cream30", "Śmietana 30%", "Świeża 30+%", "smietana do gotowania"),
        _p("cream12", "Śmietana 12%", "Świeża 12%", "smietana do kawy"),
        # czosnek: real garlic (category Czosnek) must beat a garlic-bread snack.
        _p("garlic", "Czosnek", "Czosnek", "warzywa przyprawa"),
        _p("garlicbread", "Czosnek", "Przekąski chlebowe", "pieczywo czosnkowe snack"),
    ]


@pytest.mark.parametrize(
    "query,expected_id",
    [
        ("sól", "salt-food"),
        ("szpinak", "spinach"),
        ("makaron penne", "pasta"),
        ("makaron", "pasta"),
        ("parmezan", "parmesan"),
        ("pierś z kurczaka", "breast"),
        ("śmietana 30%", "cream30"),
        ("śmietana 12%", "cream12"),
        ("czosnek", "garlic"),
    ],
)
def test_picks_the_right_product(catalogue: list[Product], query: str, expected_id: str) -> None:
    match = ProductMatcher(catalogue).match(query, "frisco")
    assert match is not None, f"{query!r} unexpectedly unmatched"
    assert match.product.id == expected_id, (
        f"{query!r} matched {match.product.name!r} ({match.product.category!r}), "
        f"expected id {expected_id!r}"
    )


def test_category_only_hit_still_matches(catalogue: list[Product]) -> None:
    # A query word that appears only in the category (not the name) should still
    # resolve — this is what makes "parmezan" -> "Ser Parmigiano Reggiano" work.
    match = ProductMatcher(catalogue).match("parmezan", "frisco")
    assert match is not None
    assert "Parmigiano" in match.product.name


def test_stopwords_do_not_create_matches(catalogue: list[Product]) -> None:
    # "z" alone is a stopword — nothing should match on it.
    assert ProductMatcher(catalogue).match("z", "frisco") is None


def test_reranker_can_override_lexical_pick(catalogue: list[Product]) -> None:
    m = ProductMatcher(catalogue)
    cands = m.match_candidates("śmietana", "frisco")
    assert len(cands) >= 2  # 30% and 12% both match

    # A re-ranker that always picks the 12% product overrides the lexical order.
    def pick_12(ingredient: str, candidates):  # noqa: ANN001
        return next((c for c in candidates if c.product.id == "cream12"), None)

    matched, _ = m.match_all(["śmietana"], "frisco", reranker=pick_12)
    assert matched[0].product.id == "cream12"


def test_reranker_decline_keeps_lexical_top(catalogue: list[Product]) -> None:
    m = ProductMatcher(catalogue)
    lexical_top = m.match("śmietana", "frisco")
    matched, _ = m.match_all(["śmietana"], "frisco", reranker=lambda i, c: None)
    assert matched[0].product.id == lexical_top.product.id
