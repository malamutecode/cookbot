import asyncio

from pydantic_ai.models.test import TestModel

from cookbot.agents.recipe_gen import build_recipe_gen_agent, recipe_gen_prompt
from cookbot.models.recipe import ParsedIngredients, Recipe, UserIntent
from cookbot.models.tenant import TenantConfig

_CONFIG = TenantConfig(
    tenant_id="test",
    persona="You are a helpful chef",
    language="en",
    recipe_source_url="",
    allowed_origins=[],
)

_INGREDIENTS = ParsedIngredients(
    items=["chicken", "spinach", "garlic"],
    must_use=["spinach"],
    dietary_hints=[],
    missing_staples=["salt", "olive oil"],
)

_INTENT = UserIntent(dish_type="stir-fry", servings=2, max_time_minutes=30, available_ingredients=["chicken", "spinach", "garlic"], free_notes="")

_RECIPE_DICT = {
    "name": "Garlic Chicken Stir-fry",
    "description": "Quick and healthy stir-fry.",
    "ingredients": ["200g chicken", "100g spinach", "2 cloves garlic", "1 tbsp olive oil", "salt to taste"],
    "steps": ["Heat oil.", "Add chicken.", "Add garlic.", "Add spinach.", "Season and serve."],
    "prep_time_minutes": 10,
    "cook_time_minutes": 15,
    "difficulty": "Easy",
    "servings": 2,
    "tips": ["Works great with rice."],
}


def _run(custom_output: dict) -> Recipe:
    agent = build_recipe_gen_agent(_CONFIG)

    async def _go() -> Recipe:
        with agent.override(model=TestModel(custom_output_args=custom_output)):
            result = await agent.run(recipe_gen_prompt(_INGREDIENTS, _INTENT))
            return result.output

    return asyncio.run(_go())


def test_recipe_gen_returns_recipe() -> None:
    result = _run(_RECIPE_DICT)
    assert isinstance(result, Recipe)


def test_recipe_gen_has_name() -> None:
    result = _run(_RECIPE_DICT)
    assert result.name != ""


def test_recipe_gen_has_at_least_three_steps() -> None:
    result = _run(_RECIPE_DICT)
    assert len(result.steps) >= 3


def test_recipe_gen_has_at_least_two_ingredients() -> None:
    result = _run(_RECIPE_DICT)
    assert len(result.ingredients) >= 2


def test_recipe_gen_difficulty_is_valid() -> None:
    result = _run(_RECIPE_DICT)
    assert result.difficulty in ("Easy", "Medium", "Hard")


def test_recipe_gen_prep_time_positive() -> None:
    result = _run(_RECIPE_DICT)
    assert result.prep_time_minutes > 0


def test_recipe_gen_cook_time_positive() -> None:
    result = _run(_RECIPE_DICT)
    assert result.cook_time_minutes > 0


def test_recipe_gen_prompt_includes_dish_type() -> None:
    prompt = recipe_gen_prompt(_INGREDIENTS, _INTENT)
    assert "stir-fry" in prompt


def test_recipe_gen_prompt_includes_must_use() -> None:
    prompt = recipe_gen_prompt(_INGREDIENTS, _INTENT)
    assert "spinach" in prompt


def test_recipe_gen_prompt_includes_time_budget() -> None:
    prompt = recipe_gen_prompt(_INGREDIENTS, _INTENT)
    assert "30" in prompt


def test_recipe_gen_prompt_includes_free_notes() -> None:
    intent_with_notes = UserIntent(
        dish_type="pasta", servings=2, max_time_minutes=0,
        available_ingredients=[], free_notes="easy to reheat"
    )
    prompt = recipe_gen_prompt(_INGREDIENTS, intent_with_notes)
    assert "reheat" in prompt
