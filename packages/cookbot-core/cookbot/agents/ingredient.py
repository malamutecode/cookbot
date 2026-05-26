from pydantic_ai import Agent

from cookbot.models.recipe import ParsedIngredients, UserIntent
from cookbot.models.tenant import TenantConfig


def build_ingredient_agent(config: TenantConfig) -> Agent[None, ParsedIngredients]:
    return Agent(
        config.model,
        output_type=ParsedIngredients,
        defer_model_check=True,
        instructions=f"""
You are {config.persona}.
You MUST respond exclusively in {config.language}. All output values must be written in {config.language}. Never use any other language.

Given a UserIntent (dish type, time budget, available ingredients), produce a structured
ParsedIngredients that the recipe pipeline will use.

Rules:
- items: all ingredients the user mentioned, normalised to lowercase singular form.
- must_use: subset of items the user specifically wants to use up (expiring, seasonal,
  explicitly said "I need to use"). Also include items from available_ingredients when
  the user's dish type implies they are central. If none, return [].
- dietary_hints: extract dietary preferences or restrictions from the dish_type or any
  context (e.g. "vegan", "gluten-free", "halal"). If none mentioned, return [].
- missing_staples: common pantry items almost certainly needed for the dish that the user
  didn't mention. Only include items you are highly confident about (salt, oil, pepper,
  garlic for most savoury dishes). Do NOT add specialty ingredients.

If available_ingredients is empty and dish_type is "any", return all fields as empty lists.
""",
    )


def intent_to_prompt(intent: UserIntent) -> str:
    time_str = f"{intent.max_time_minutes} minutes" if intent.max_time_minutes else "no time limit"
    servings_str = str(intent.servings) if intent.servings else "not specified"
    ingredients_str = ", ".join(intent.available_ingredients) if intent.available_ingredients else "none specified"
    notes_str = intent.free_notes if intent.free_notes else "none"
    return (
        f"Dish type: {intent.dish_type}\n"
        f"Servings: {servings_str}\n"
        f"Time available: {time_str}\n"
        f"Available ingredients: {ingredients_str}\n"
        f"Extra notes: {notes_str}"
    )
