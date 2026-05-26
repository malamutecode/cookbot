from pydantic_ai import Agent

from cookbot.models.recipe import Recipe
from cookbot.models.tenant import TenantConfig


def build_refinement_agent(config: TenantConfig) -> Agent[None, Recipe]:
    return Agent(
        config.model,
        output_type=Recipe,
        defer_model_check=True,
        instructions=f"""
You are {config.persona}.
You MUST respond exclusively in {config.language}. Every field in the output — recipe name, description, ingredients, steps, tips — must be written in {config.language}. Never use any other language.

Apply the requested modification to the recipe and return the updated version.

Rules:
- Preserve the recipe structure exactly (same Pydantic fields).
- Apply the modification faithfully and practically.
- Keep the recipe realistic for home cooking.
- difficulty, prep_time_minutes, cook_time_minutes and servings should be updated
  if the modification affects them (e.g. "make it faster" → reduce cook time).
- Never remove all steps or ingredients — always return a complete, usable recipe.
""",
    )


def refinement_prompt(recipe: Recipe, modification: str) -> str:
    return (
        f"Recipe:\n{recipe.model_dump_json(indent=2)}\n\n"
        f"Modification requested: {modification}"
    )
