"""Grocery matching route — resolve a shopping list to a delivery shop's products.

Thin client route (Architecture Rule 1): all lexical matching lives in the
delivery-shops package. This module enforces the tenant's enabled shops, caches the
per-shop matcher on ``app.state``, optionally LLM-re-ranks ambiguous shortlists,
and returns the package's model.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from cookbot.agents.product_rerank import build_product_rerank_agent
from delivery_shops.base import get_shop
from delivery_shops.matcher import ProductMatcher
from delivery_shops.models import GroceryMatchResult, ProductMatch, UnmatchedItem

log = structlog.get_logger()

router = APIRouter()

# How many lexical candidates to shortlist per ingredient and hand to the re-ranker.
_CANDIDATE_LIMIT = 5


class MatchRequest(BaseModel):
    ingredients: list[str]


class _CachedMatcher:
    """Per-shop matcher cache keyed on the feed's ``generated_at`` stamp."""

    def __init__(self) -> None:
        self.matcher: ProductMatcher | None = None
        self.generated_at: str | None = None
        self.lock = asyncio.Lock()


async def _get_matcher(request: Request, shop_id: str) -> tuple[ProductMatcher, str | None]:
    """Load (and cache) the matcher for a shop, rebuilding only when the feed
    timestamp changes. The shop object owns the feed's own TTL cache."""
    caches: dict[str, _CachedMatcher] = getattr(request.app.state, "grocery_matchers", None) or {}
    cache = caches.get(shop_id)
    if cache is None:
        cache = _CachedMatcher()
        caches[shop_id] = cache
        request.app.state.grocery_matchers = caches

    shop = get_shop(shop_id)
    async with cache.lock:
        products, generated_at = await shop.load()
        if cache.matcher is None or cache.generated_at != generated_at:
            cache.matcher = ProductMatcher(products)
            cache.generated_at = generated_at
        return cache.matcher, generated_at


async def _rerank(ingredient: str, candidates: list[ProductMatch], agent) -> ProductMatch:
    """Pick the best candidate via the LLM, falling back to lexical #1.

    Contains its own failures — an agent error must never break a match, so we
    return the lexical top pick on any problem."""
    numbered = "\n".join(
        f"{i}. {c.product.name} [{c.product.category}]" for i, c in enumerate(candidates, start=1)
    )
    prompt = f"Ingredient: {ingredient}\nCandidates:\n{numbered}"
    try:
        result = await agent.run(prompt)
        choice = result.output.choice
        if choice is not None and 1 <= choice <= len(candidates):
            return candidates[choice - 1]
    except Exception as exc:  # noqa: BLE001 — re-rank is best-effort
        log.warning("product_rerank_failed", ingredient=ingredient, error=str(exc))
    return candidates[0]


@router.post("/grocery/{shop}/match", response_model=GroceryMatchResult)
async def match_grocery(shop: str, body: MatchRequest, request: Request) -> GroceryMatchResult:
    from app.config.tenant import TASTYHUB_CONFIG

    if shop not in TASTYHUB_CONFIG.delivery_shops:
        raise HTTPException(status_code=404, detail=f"Delivery shop not enabled: {shop}")

    if not body.ingredients:
        return GroceryMatchResult(shop=shop, matched=[], unmatched=[])

    matcher, generated_at = await _get_matcher(request, shop)

    # Lexical shortlist per ingredient.
    shortlists = {ing: matcher.match_candidates(ing, shop, limit=_CANDIDATE_LIMIT) for ing in body.ingredients}
    unmatched = [UnmatchedItem(ingredient=ing) for ing, cands in shortlists.items() if not cands]

    # LLM-re-rank only the ambiguous ones (>1 candidate), concurrently. Unambiguous
    # matches (single candidate) skip the LLM entirely to save tokens.
    agent = build_product_rerank_agent(TASTYHUB_CONFIG)
    ambiguous = {ing: c for ing, c in shortlists.items() if len(c) > 1}
    reranked = dict(
        zip(
            ambiguous.keys(),
            await asyncio.gather(*(_rerank(ing, c, agent) for ing, c in ambiguous.items())),
        )
    )

    matched: list[ProductMatch] = []
    for ing, cands in shortlists.items():
        if not cands:
            continue
        matched.append(reranked.get(ing, cands[0]))

    return GroceryMatchResult(
        shop=shop,
        matched=matched,
        unmatched=unmatched,
        generated_at=generated_at,
    )
