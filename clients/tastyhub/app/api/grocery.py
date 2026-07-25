"""Grocery matching route — resolve a shopping list to a delivery shop's products.

Thin client route (Architecture Rule 1): all matching lives in the delivery-shops
package. This module enforces the tenant's enabled shops, prefers a shop's own
search backend when it has one, falls back to the cached feed matcher when that
fails, optionally LLM-re-ranks, and returns the package's model.

Two matching paths (STEP 50):

* **Search** — the shop resolves each ingredient itself and ranks the results.
  No local index, live prices/stock, so ``generated_at`` is ``None``. Re-ranking
  is off unless ``TenantConfig.grocery_llm_rerank`` is set.
* **Feed** — the historical path: download the catalogue, build a
  ``ProductMatcher``, re-rank ambiguous lexical shortlists with the LLM. Used for
  feed-only shops and whenever the search backend is unreachable.
"""

from __future__ import annotations

import asyncio

import structlog
from cookbot.agents.product_rerank import build_product_rerank_agent
from delivery_shops.base import get_shop, supports_search
from delivery_shops.matcher import ProductMatcher
from delivery_shops.models import GroceryMatchResult, ProductMatch, UnmatchedItem
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = structlog.get_logger()

# NOTE (STEP 44): deliberately NOT gated by require_password_set. Like
# /v1/shopping-list, this route carries no user identity — it matches an
# ingredient list posted in the body against a public product feed and is
# reachable with only the widget's API key. A locked account is stopped at
# /v1/sessions and the WS handshake.
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


async def _resolve(
    shortlists: dict[str, list[ProductMatch]],
    config,  # noqa: ANN001 — TenantConfig, imported lazily by the caller
    *,
    rerank: bool,
) -> tuple[list[ProductMatch], list[UnmatchedItem]]:
    """Collapse per-ingredient shortlists to one pick each, splitting out misses.

    When ``rerank`` is set, the ambiguous shortlists (>1 candidate) go to the LLM
    concurrently; unambiguous ones skip it entirely to save tokens.
    """
    unmatched = [UnmatchedItem(ingredient=ing) for ing, cands in shortlists.items() if not cands]

    reranked: dict[str, ProductMatch] = {}
    if rerank:
        agent = build_product_rerank_agent(config)
        ambiguous = {ing: c for ing, c in shortlists.items() if len(c) > 1}
        reranked = dict(
            zip(
                ambiguous.keys(),
                await asyncio.gather(*(_rerank(ing, c, agent) for ing, c in ambiguous.items())),
            )
        )

    matched = [
        reranked.get(ing, cands[0]) for ing, cands in shortlists.items() if cands
    ]
    return matched, unmatched


async def _match_via_feed(
    shop: str, ingredients: list[str], request: Request, config
) -> GroceryMatchResult:  # noqa: ANN001
    """Historical path: local lexical matching over the shop's downloaded feed."""
    matcher, generated_at = await _get_matcher(request, shop)
    shortlists = {
        ing: matcher.match_candidates(ing, shop, limit=_CANDIDATE_LIMIT) for ing in ingredients
    }
    # Lexical shortlists are genuinely ambiguous, so this path always re-ranks.
    matched, unmatched = await _resolve(shortlists, config, rerank=True)
    return GroceryMatchResult(
        shop=shop, matched=matched, unmatched=unmatched, generated_at=generated_at
    )


@router.post("/grocery/{shop}/match", response_model=GroceryMatchResult)
async def match_grocery(shop: str, body: MatchRequest, request: Request) -> GroceryMatchResult:
    from app.config.tenant import TASTYHUB_CONFIG

    if shop not in TASTYHUB_CONFIG.delivery_shops:
        raise HTTPException(status_code=404, detail=f"Delivery shop not enabled: {shop}")

    if not body.ingredients:
        return GroceryMatchResult(shop=shop, matched=[], unmatched=[])

    shop_impl = get_shop(shop)

    if supports_search(shop_impl):
        try:
            shortlists = await shop_impl.search_many(body.ingredients, limit=_CANDIDATE_LIMIT)
        except Exception as exc:  # noqa: BLE001 — any backend failure ⇒ use the feed
            log.warning("grocery_search_unavailable_using_feed", shop=shop, error=str(exc))
        else:
            matched, unmatched = await _resolve(
                shortlists, TASTYHUB_CONFIG, rerank=TASTYHUB_CONFIG.grocery_llm_rerank
            )
            # Results are live, so there is no catalogue date to report.
            return GroceryMatchResult(
                shop=shop, matched=matched, unmatched=unmatched, generated_at=None
            )

    return await _match_via_feed(shop, body.ingredients, request, TASTYHUB_CONFIG)
