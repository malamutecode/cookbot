"""Lexical ingredient -> product matcher.

Shop-agnostic: built from a ``list[Product]`` and knows nothing about any specific
shop. Matching is purely lexical (word overlap), not semantic — cheap and fast, no
vector DB. See the module docstring of :mod:`delivery_shops` for the trade-offs.

Algorithm (validated against the live Frisco feed):

1. Normalise product and query text the same way: lowercase, strip diacritics
   (NFKD ascii-fold), drop punctuation, split into a token set.
2. Index each product on ``name`` + ``keywords`` + ``category`` combined, so a
   query word can be found wherever it lives (the product name, its SEO keywords,
   or its shelf category).
3. Build an inverted index ``token -> {product indices}`` so a query only scores
   products that share at least one word (~dozens, not the whole catalogue).
4. Score candidates by *where* the overlap lands — a query word in the product
   **name** is worth far more than one that only appears in the keyword soup:
     - ``_NAME_TOKEN_WEIGHT`` per query word found in the name,
     - ``_CATEGORY_TOKEN_WEIGHT`` per query word found in the category,
     - ``_KEYWORD_TOKEN_WEIGHT`` per query word found only in keywords,
     - ``_NAME_SUBSTR_BOOST`` when the whole query is a substring of the name,
     - ``_NAME_PREFIX_BOOST`` when the name *starts* with the query,
     - ``_EXTRA_NAME_TOKEN_PENALTY`` per unmatched name token, so a plain
       "Sól morska" outranks "Sól ochronna do zmywarki",
     - a small availability nudge.
   This is what stops ``sól`` -> dishwasher salt, ``szpinak`` -> baby food, and
   ``makaron`` -> sausage: the wrong products only matched in keywords / carried
   noise tokens, while the right ones match in the name and category.
5. Best scorer above ``_MIN_SCORE`` wins; otherwise the ingredient is unmatched.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Callable

from delivery_shops.models import Product, ProductMatch, UnmatchedItem

# A re-ranker is injected by the caller (e.g. an LLM-backed picker) to choose the
# best of the lexical shortlist, or return None to keep the lexical #1. The
# delivery-shops package stays LLM-agnostic — it only defines this seam.
ReRanker = Callable[[str, list[ProductMatch]], "ProductMatch | None"]

# Where a query word lands matters: name >> category >> keywords.
_NAME_TOKEN_WEIGHT = 3.0
_CATEGORY_TOKEN_WEIGHT = 3.0
_KEYWORD_TOKEN_WEIGHT = 0.5
# Whole-query shape bonuses.
_NAME_SUBSTR_BOOST = 2.0
_NAME_PREFIX_BOOST = 2.0
# How fully the query covers the product name's own identity (plain "Masło" for
# "masło" beats "Masło z czosnkiem"). Scaled by the covered fraction. Kept small
# so it only breaks near-ties and never outweighs a real category match — that
# would let "Czosnek" (garlic-bread snack) beat real garlic in category "Czosnek".
_NAME_COVERAGE_BOOST = 1.5
# Plainer products (fewer leftover name words) win ties, capped so a long but
# genuinely relevant name isn't buried.
_EXTRA_NAME_TOKEN_PENALTY = 0.35
_MAX_EXTRA_TOKEN_PENALTY = 3.0
_AVAILABILITY_BONUS = 0.5
# A keyword-only hit (no name/category overlap) is too weak to trust on its own.
_MIN_SCORE = 2.0

_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")

# Polish (+ a few English) function words carry no matching signal and, worse,
# match almost everything — e.g. "z" in "pierś z kurczaka" would tie liver and
# breast. Dropped from both product and query tokens. Diacritic-folded already.
_STOPWORDS = frozenset(
    {
        "z", "ze", "i", "w", "we", "na", "do", "od", "bez", "oraz", "lub", "a", "o",
        "u", "po", "przy", "dla", "the", "of", "and", "with", "szt", "g", "kg", "ml", "l",
    }
)


def _fold(text: str) -> str:
    """Lowercase, strip diacritics, and reduce to ``[a-z0-9 ]`` — the shared
    normalisation for both product text and queries (``mąka`` -> ``maka``)."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM.sub(" ", ascii_only.lower()).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _fold(text).split() if t and t not in _STOPWORDS}


class ProductMatcher:
    """Resolves free-text ingredient strings to their best-matching product."""

    def __init__(self, products: list[Product]) -> None:
        self._products = products
        # Per-product precomputed match material. Name, category and keyword tokens
        # are kept separate so scoring can weight *where* a query word lands.
        self._names_folded: list[str] = []
        self._name_tokens: list[set[str]] = []
        self._category_tokens: list[set[str]] = []
        self._keyword_tokens: list[set[str]] = []
        self._index: dict[str, set[int]] = defaultdict(set)
        for idx, product in enumerate(products):
            name_toks = _tokens(product.name)
            cat_toks = _tokens(product.category)
            kw_toks = _tokens(product.keywords)
            self._names_folded.append(_fold(product.name))
            self._name_tokens.append(name_toks)
            self._category_tokens.append(cat_toks)
            self._keyword_tokens.append(kw_toks)
            for tok in name_toks | cat_toks | kw_toks:
                self._index[tok].add(idx)

    def _score(self, idx: int, query_folded: str, query_tokens: set[str]) -> float:
        name_toks = self._name_tokens[idx]
        cat_toks = self._category_tokens[idx]
        kw_toks = self._keyword_tokens[idx]

        in_name = query_tokens & name_toks
        in_cat = query_tokens & cat_toks
        # Keyword hits only count for words not already found in name/category.
        in_kw_only = (query_tokens & kw_toks) - in_name - in_cat

        score = (
            len(in_name) * _NAME_TOKEN_WEIGHT
            + len(in_cat) * _CATEGORY_TOKEN_WEIGHT
            + len(in_kw_only) * _KEYWORD_TOKEN_WEIGHT
        )

        name_folded = self._names_folded[idx]
        if query_folded and query_folded in name_folded:
            score += _NAME_SUBSTR_BOOST
        if name_folded.startswith(query_folded):
            score += _NAME_PREFIX_BOOST

        if in_name:
            # Reward how much of the *name's* identity the query covers: a name
            # that is entirely the ingredient ("Masło" for "masło") beats one where
            # the ingredient is a modifier ("Masło z czosnkiem"). Fraction in
            # [0, 1] scaled to a bonus.
            coverage = len(in_name) / len(name_toks) if name_toks else 0.0
            score += coverage * _NAME_COVERAGE_BOOST
            # And still penalise leftover name words, capped.
            extra = len(name_toks) - len(in_name)
            score -= min(extra * _EXTRA_NAME_TOKEN_PENALTY, _MAX_EXTRA_TOKEN_PENALTY)

        if self._products[idx].available:
            score += _AVAILABILITY_BONUS
        return score

    def match_candidates(
        self, ingredient: str, shop: str, limit: int = 5
    ) -> list[ProductMatch]:
        """Return up to ``limit`` best candidate products, best first.

        This is the raw ranked shortlist the single-best ``match`` picks from, and
        also what an optional LLM re-ranker inspects to resolve ambiguous cases the
        lexical score can't (e.g. plain butter vs. "Masło z czosnkiem")."""
        query_folded = _fold(ingredient)
        query_tokens = _tokens(ingredient)
        if not query_tokens:
            return []

        candidate_idxs: set[int] = set()
        for tok in query_tokens:
            candidate_idxs |= self._index.get(tok, set())

        scored: list[tuple[float, int]] = []
        for idx in candidate_idxs:
            score = self._score(idx, query_folded, query_tokens)
            if score >= _MIN_SCORE:
                scored.append((score, idx))
        # Sort by score desc, then plainer (shorter) name, then index for stability.
        scored.sort(key=lambda si: (-si[0], len(self._name_tokens[si[1]]), si[1]))

        return [
            ProductMatch(
                ingredient=ingredient,
                shop=shop,
                product=self._products[idx],
                score=score,
            )
            for score, idx in scored[:limit]
        ]

    def match(self, ingredient: str, shop: str) -> ProductMatch | None:
        """Return the single best product match for one ingredient, or ``None``."""
        candidates = self.match_candidates(ingredient, shop, limit=1)
        return candidates[0] if candidates else None

    def match_all(
        self,
        ingredients: list[str],
        shop: str,
        reranker: ReRanker | None = None,
        candidate_limit: int = 5,
    ) -> tuple[list[ProductMatch], list[UnmatchedItem]]:
        """Match a whole shopping list, splitting into matched / unmatched.

        When ``reranker`` is given, each ingredient's top ``candidate_limit``
        lexical candidates are handed to it to pick the best (or reject all); the
        lexical #1 is used as a fallback if the re-ranker declines to choose."""
        matched: list[ProductMatch] = []
        unmatched: list[UnmatchedItem] = []
        for ingredient in ingredients:
            candidates = self.match_candidates(ingredient, shop, limit=candidate_limit)
            if not candidates:
                unmatched.append(UnmatchedItem(ingredient=ingredient))
                continue
            chosen = candidates[0]
            if reranker is not None and len(candidates) > 1:
                picked = reranker(ingredient, candidates)
                if picked is not None:
                    chosen = picked
            matched.append(chosen)
        return matched, unmatched
