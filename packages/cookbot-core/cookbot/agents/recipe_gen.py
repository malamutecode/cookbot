from pydantic_ai import Agent

from cookbot.models.recipe import ParsedIngredients, Recipe, UserIntent
from cookbot.models.tenant import TenantConfig


def build_recipe_gen_agent(config: TenantConfig) -> Agent[None, Recipe]:
    return Agent(
        config.model_recipe_gen,
        output_type=Recipe,
        defer_model_check=True,
        instructions=f"""
You are {config.persona}.
You MUST respond exclusively in {config.language}. Every field in the output — recipe name, description, ingredients, steps, tips — must be written in {config.language}. Never use any other language.

Generate a complete, practical home-cooking recipe from the provided ingredients and context.

Rules:
- name: short, appetising title.
- description: 1-2 sentences describing the dish and why it works.
- ingredients: include exact quantities (e.g. "200g chicken breast", "2 cloves garlic, minced").
  Include both the user's available ingredients and any missing_staples.
- steps: numbered, actionable steps. At least 4. Be specific about heat, timing, technique.
- prep_time_minutes / cook_time_minutes: realistic integer estimates.
- difficulty: exactly one of "Easy", "Medium", or "Hard".
- servings: match the requested number; default to 2 if not specified.
- tips: 1-3 practical tips (storage, substitutions, serving suggestions). Can be empty list.

Respect any dietary hints and free notes from the user.
Stay within the time budget if one is given.
""",
    )


def recipe_gen_prompt(ingredients: ParsedIngredients, intent: UserIntent) -> str:
    all_items = ingredients.items + [s for s in ingredients.missing_staples if s not in ingredients.items]
    time_str = f"Time budget: {intent.max_time_minutes} minutes." if intent.max_time_minutes else ""
    dietary_str = f"Dietary: {', '.join(ingredients.dietary_hints)}." if ingredients.dietary_hints else ""
    notes_str = f"Extra notes: {intent.free_notes}." if intent.free_notes else ""
    servings_str = f"Servings: {intent.servings}." if intent.servings else ""
    must_use_str = f"Must use: {', '.join(ingredients.must_use)}." if ingredients.must_use else ""
    return "\n".join(filter(None, [
        f"Dish type: {intent.dish_type}.",
        f"Ingredients available: {', '.join(all_items)}.",
        must_use_str,
        dietary_str,
        time_str,
        servings_str,
        notes_str,
    ]))
