from __future__ import annotations

import asyncio
import re

import httpx
import structlog
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool

from cookbot.models.recipe import ParsedIngredients, RecipeSummary, UserIntent
from cookbot.models.tenant import TenantConfig

log = structlog.get_logger()

_OPTIONS_MAX_RESULTS = 5

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
_OG_FETCH_TIMEOUT = 6.0          # per page; images are nice-to-have, fail fast
_OG_MAX_BYTES = 60_000           # og:image lives in <head>; no need to read more


async def _fetch_og_image(client: httpx.AsyncClient, url: str) -> str | None:
    """Best-effort fetch of a page's og:image. Returns None on any failure."""
    try:
        resp = await client.get(url, headers=_OG_FETCH_HEADERS)
        head = resp.text[:_OG_MAX_BYTES]
        m = _OG_IMAGE_RE.search(head)
        return m.group(1) if m else None
    except Exception:
        return None


async def populate_proposal_images(proposals: list[RecipeSummary]) -> None:
    """Fill image_url for web_search proposals by fetching each page's og:image
    concurrently. In-place, best-effort: a proposal that fails keeps image_url=None
    (the frontend shows a placeholder). AI proposals are skipped (no source page)."""
    targets = [
        p for p in proposals
        if p.source == "web_search" and p.source_url and not p.image_url
    ]
    if not targets:
        return
    async with httpx.AsyncClient(follow_redirects=True, timeout=_OG_FETCH_TIMEOUT) as client:
        results = await asyncio.gather(
            *(_fetch_og_image(client, p.source_url) for p in targets),  # type: ignore[arg-type]
            return_exceptions=True,
        )
    for p, img in zip(targets, results):
        if isinstance(img, str) and img:
            p.image_url = img
    log.info("populate_proposal_images",
             targets=len(targets),
             filled=sum(1 for p in targets if p.image_url))


class RecipeSummaryList(BaseModel):
    proposals: list[RecipeSummary]


def build_recipe_options_agent(config: TenantConfig) -> Agent[None, RecipeSummaryList]:
    return Agent(
        config.model_recipe_options,
        output_type=RecipeSummaryList,
        defer_model_check=True,
        tools=[duckduckgo_search_tool(max_results=_OPTIONS_MAX_RESULTS)],
        instructions=f"""You are {config.persona}.
You MUST respond exclusively in {config.language}.

Your task: propose 4 recipe options that match the user's request.

PRIORITISE REAL RECIPES FROM THE WEB. AI-generated proposals are a last-resort
top-up, NOT the default. Always search first and use what you find.

Steps:
1. ALWAYS call `duckduckgo_search` first with the SEARCH QUERY from the prompt.
   You MUST search before proposing anything.
2. Read all results and pick pages that are a SINGLE recipe with a real
   ingredient list and steps — not articles about food.
   PREFER URLs that look like a recipe page, e.g. containing "/przepis/",
   "/przepisy/<slug>", "/recipe/". A recipe site's dedicated recipe page
   (aniagotuje.pl/przepis/..., kwestiasmaku.com/przepis/...) is ideal.
   AVOID, even if the title mentions the dish:
     - news/magazine articles and blog round-ups ("Top 10…", "Szybki obiad: …",
       lifestyle portals like ofeminin.pl, magazine sections),
     - forums, comment threads, listicles, category/tag/search pages,
       aggregator or site homepages.
   If the prompt lists PREFERRED SITES, rank pages from those domains first — but
   still use good single-recipe results from any site.
3. Turn as many good search results as possible into web_search proposals (aim for
   all 4 from the web). Whether AI-generated proposals are allowed:
   - If AI IS allowed: only AFTER using every good web result, fill any remaining
     slots up to 4 with ai_generated proposals. If you found 4 good pages, return
     4 web_search proposals and zero AI ones.
   - If AI is NOT allowed: only return web_search proposals you found. Return fewer
     than 4 if you cannot find enough good results — never invent one.
4. For each proposal fill ALL fields:
   - name: recipe name in {config.language}
   - description: 1-2 sentence appetising summary in {config.language}
   - difficulty: "Łatwe" / "Średnie" / "Trudne"
   - total_time_minutes: realistic integer
   - key_ingredients: 3-5 main ingredients
   - source: "web_search" or "ai_generated"
   - source_url: REQUIRED for every web_search proposal — copy the exact result URL
     from duckduckgo_search. A web_search proposal without a source_url is invalid;
     if you cannot provide a real URL, make it an ai_generated proposal instead.
     Always null for ai_generated proposals.
   - image_url: ALWAYS null. Images are populated automatically afterwards from
     each web page's og:image — do not attempt to provide one.

Rules:
- Vary the options: different cooking styles or difficulty levels.
- All text fields must be in {config.language}.
- Leave image_url null for every proposal; image population happens downstream.""",
    )


def recipe_options_prompt(
    ingredients: ParsedIngredients,
    intent: UserIntent,
    site_filter: str = "",
    allow_ai_generated: bool = True,
    preferred_sites: list[str] | None = None,
) -> str:
    dish = intent.dish_type if intent.dish_type != "any" else "przepis"
    ingr = f" {', '.join(ingredients.items)}" if ingredients.items else ""
    base_query = f"{dish}{ingr}"

    # Hard restriction (sites_only) vs. open web. We deliberately do NOT wrap a
    # `site:a OR site:b` filter in parentheses — that form is unreliable on DDG.
    if site_filter:
        search_query = f"{base_query} {site_filter}"
    else:
        search_query = base_query

    parts = [f'Search query to use verbatim: "{search_query}"']
    if preferred_sites:
        parts.append(
            "Preferred sites (rank these domains first, but use any good result): "
            + ", ".join(preferred_sites)
        )
    if allow_ai_generated:
        parts.append("AI-generated proposals: ALLOWED — but only to top up after web results.")
    else:
        parts.append('AI-generated proposals: NOT ALLOWED — only web_search proposals.')
    if ingredients.dietary_hints:
        parts.append(f"Dietary requirements: {', '.join(ingredients.dietary_hints)}")
    if intent.max_time_minutes:
        parts.append(f"Time budget: under {intent.max_time_minutes} minutes")
    if intent.servings:
        parts.append(f"Servings: {intent.servings}")
    if intent.free_notes:
        parts.append(f"Notes: {intent.free_notes}")
    return "\n".join(parts)
