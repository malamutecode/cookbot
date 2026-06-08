from __future__ import annotations

import structlog

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool

from cookbot.models.recipe import ParsedIngredients, RecipeSummary, UserIntent
from cookbot.models.tenant import TenantConfig

log = structlog.get_logger()

_OPTIONS_MAX_RESULTS = 5


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
   - image_url: null (images are loaded separately)

Rules:
- Vary the options: different cooking styles or difficulty levels.
- All text fields must be in {config.language}.
- Do NOT call any image search — leave image_url as null for all proposals.""",
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
