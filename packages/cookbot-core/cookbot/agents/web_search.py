from pydantic_ai import Agent
from pydantic_ai.common_tools.duckduckgo import duckduckgo_search_tool

from cookbot.models.recipe import ParsedIngredients, Recipe, UserIntent
from cookbot.models.tenant import TenantConfig


def build_web_search_agent(config: TenantConfig) -> Agent[None, Recipe | None]:
    return Agent(
        config.model,
        output_type=Recipe | None,
        defer_model_check=True,
        tools=[duckduckgo_search_tool()],
        instructions=f"""
You are {config.persona}.
You MUST respond exclusively in {config.language}. Every field in the output — recipe name, description, ingredients, steps, tips — must be written in {config.language}. Never use any other language.

Your task: search the web for a real recipe that matches the user's ingredients and preferences.

Steps:
1. Perform a web search using the provided ingredients and dish type.
2. If you find a good match, extract a complete Recipe with ALL fields filled:
   name, description, ingredients (with quantities), steps (numbered, actionable),
   prep_time_minutes, cook_time_minutes, difficulty (Easy/Medium/Hard), servings, tips.
3. If search results are empty, irrelevant, or not a real recipe, return null.

Rules:
- Only return a Recipe if you are confident it matches the request well.
- Fill ALL fields — never leave ingredients or steps empty.
- Scale ingredient quantities to match the requested servings if specified.
- Return null rather than inventing a recipe — that is RecipeGenAgent's job.
""",
    )


def web_search_prompt(ingredients: ParsedIngredients, intent: UserIntent) -> str:
    parts = [f"Find a {intent.dish_type} recipe" if intent.dish_type != "any" else "Find a recipe"]
    if ingredients.items:
        parts.append(f"using: {', '.join(ingredients.items)}")
    if ingredients.dietary_hints:
        parts.append(f"dietary: {', '.join(ingredients.dietary_hints)}")
    if intent.max_time_minutes:
        parts.append(f"ready in under {intent.max_time_minutes} minutes")
    if intent.servings:
        parts.append(f"for {intent.servings} servings")
    if intent.free_notes:
        parts.append(f"notes: {intent.free_notes}")
    return ". ".join(parts) + "."
