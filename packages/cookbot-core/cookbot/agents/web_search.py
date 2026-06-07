from pydantic_ai import Agent
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool
from pydantic_ai.common_tools.web_fetch import web_fetch_tool

from cookbot.models.recipe import ParsedIngredients, Recipe, UserIntent
from cookbot.models.tenant import TenantConfig

_WEB_SEARCH_MAX_RESULTS = 5
_MAX_PAGE_CONTENT = 12_000

_EXTRACT_INSTRUCTIONS = """
## Steps
1. **Fetch the page**: call `web_fetch` with the URL provided in the prompt.
2. **Extract the recipe** from the fetched markdown:
   - name, description
   - ingredients: exact quantities as written
   - steps: numbered, actionable, as written on the page
   - prep_time_minutes, cook_time_minutes (integers)
   - difficulty: exactly "Easy", "Medium", or "Hard"
   - servings: integer
   - tips: practical tips from the page (empty list if none)
   - source_url: the URL you fetched (copy exactly)
   - image_url: og:image URL if visible in the markdown; otherwise null
3. If the page has no real recipe (404, paywall, unrelated), return null.

## Rules
- NEVER invent ingredients or steps — extract only what is on the page.
- Always set source_url to the exact URL fetched.
"""

_SEARCH_INSTRUCTIONS = """
## Steps
1. **Search**: use `duckduckgo_search` with the query provided verbatim.
2. **Pick the best URL**: choose the result most likely to be a real recipe page.
   Prefer domains mentioned in the query (site: operators).
   Avoid aggregator homepages, forum threads, or listicles.
3. **Fetch the page**: call `web_fetch` with that URL.
4. **Extract the recipe** from the fetched markdown:
   - name, description
   - ingredients: exact quantities as written
   - steps: numbered, actionable, as written on the page
   - prep_time_minutes, cook_time_minutes (integers)
   - difficulty: exactly "Easy", "Medium", or "Hard"
   - servings: integer
   - tips: practical tips from the page (empty list if none)
   - source_url: the URL you fetched (copy exactly)
   - image_url: og:image URL if visible in the markdown; otherwise null
5. If the page has no real recipe, try the second-best URL from step 1.
   If none work, return null.

## Rules
- NEVER invent ingredients or steps — extract only what is on the page.
- Always set source_url to the exact URL fetched.
"""


def build_web_search_agent(config: TenantConfig) -> Agent[None, Recipe | None]:
    """Agent that searches DDG then fetches and extracts a recipe from the best result."""
    return Agent(
        config.model_web_search,
        output_type=Recipe | None,
        defer_model_check=True,
        tools=[
            duckduckgo_search_tool(max_results=_WEB_SEARCH_MAX_RESULTS),
            web_fetch_tool(max_content_length=_MAX_PAGE_CONTENT),
        ],
        instructions=(
            f"You are {config.persona}.\n"
            f"You MUST respond exclusively in {config.language}. "
            f"Every field — name, description, ingredients, steps, tips — must be in {config.language}.\n"
            + _SEARCH_INSTRUCTIONS
        ),
    )


def build_web_fetch_agent(config: TenantConfig) -> Agent[None, Recipe | None]:
    """Agent that fetches a known URL and extracts the recipe — no search needed."""
    return Agent(
        config.model_web_search,
        output_type=Recipe | None,
        defer_model_check=True,
        tools=[web_fetch_tool(max_content_length=_MAX_PAGE_CONTENT)],
        instructions=(
            f"You are {config.persona}.\n"
            f"You MUST respond exclusively in {config.language}. "
            f"Every field — name, description, ingredients, steps, tips — must be in {config.language}.\n"
            + _EXTRACT_INSTRUCTIONS
        ),
    )


def web_search_prompt(ingredients: ParsedIngredients, intent: UserIntent, site_filter: str = "") -> str:
    dish = intent.dish_type if intent.dish_type != "any" else "przepis"
    ingr = f" {', '.join(ingredients.items)}" if ingredients.items else ""
    base_query = f"{dish}{ingr}"

    if site_filter:
        search_query = f"({site_filter}) {base_query}"
    else:
        search_query = base_query

    parts = [f'Search query to use verbatim: "{search_query}"']
    if ingredients.dietary_hints:
        parts.append(f"Dietary requirements: {', '.join(ingredients.dietary_hints)}")
    if intent.max_time_minutes:
        parts.append(f"Time budget: under {intent.max_time_minutes} minutes")
    if intent.servings:
        parts.append(f"Servings: {intent.servings}")
    if intent.free_notes:
        parts.append(f"Notes: {intent.free_notes}")
    return "\n".join(parts)


def web_fetch_prompt(url: str, servings: int = 2) -> str:
    return f"Fetch and extract the recipe from this URL: {url}\nAdjust servings to: {servings}"
