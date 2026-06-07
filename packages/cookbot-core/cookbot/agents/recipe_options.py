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

Your task: propose up to 4 recipe options that match the user's request.

Steps:
1. The prompt gives you a SEARCH QUERY — use it verbatim with `duckduckgo_search`.
2. Read all results. Pick the best matching real recipe pages (prefer actual recipe sites,
   avoid forums, listicles, or aggregator homepages).
3. The prompt says whether AI-generated proposals are allowed.
   - If AI IS allowed: fill remaining slots up to 4 with ai_generated proposals.
   - If AI is NOT allowed: only return web_search proposals you found. Return fewer than 4
     if you cannot find enough good results.
4. For each proposal fill ALL fields:
   - name: recipe name in {config.language}
   - description: 1-2 sentence appetising summary in {config.language}
   - difficulty: "Łatwe" / "Średnie" / "Trudne"
   - total_time_minutes: realistic integer
   - key_ingredients: 3-5 main ingredients
   - source: "web_search" or "ai_generated"
   - source_url: the exact URL from the search result for web_search proposals; null for ai_generated
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
) -> str:
    dish = intent.dish_type if intent.dish_type != "any" else "przepis"
    ingr = f" {', '.join(ingredients.items)}" if ingredients.items else ""
    base_query = f"{dish}{ingr}"

    if site_filter:
        search_query = f"({site_filter}) {base_query}"
    else:
        search_query = base_query

    parts = [f'Search query to use verbatim: "{search_query}"']
    if allow_ai_generated:
        parts.append("AI-generated proposals: ALLOWED — fill remaining slots up to 4.")
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
