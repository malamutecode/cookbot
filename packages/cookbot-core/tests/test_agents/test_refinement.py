import asyncio

from pydantic_ai.models.test import TestModel

from cookbot.agents.refinement import build_refinement_agent, refinement_prompt
from cookbot.models.recipe import Recipe
from cookbot.models.tenant import TenantConfig

_CONFIG = TenantConfig(
    tenant_id="test",
    persona="You are a helpful chef",
    language="en",
    recipe_source_url="",
    allowed_origins=[],
)

_BASE_RECIPE = Recipe(
    name="Chicken Stir-fry",
    description="Quick chicken stir-fry with garlic.",
    ingredients=["200g chicken breast", "2 cloves garlic", "1 tbsp oil"],
    steps=["Heat oil.", "Cook chicken.", "Add garlic.", "Serve."],
    prep_time_minutes=10,
    cook_time_minutes=15,
    difficulty="Easy",
    servings=2,
    tips=[],
)


def _run(modification: str, custom_output: dict) -> Recipe:
    agent = build_refinement_agent(_CONFIG)

    async def _go() -> Recipe:
        with agent.override(model=TestModel(custom_output_args=custom_output)):
            result = await agent.run(refinement_prompt(_BASE_RECIPE, modification))
            return result.output

    return asyncio.run(_go())


def test_refinement_returns_valid_recipe() -> None:
    result = _run(
        "make it vegan",
        {**_BASE_RECIPE.model_dump(), "ingredients": ["200g tofu", "2 cloves garlic", "1 tbsp oil"], "name": "Tofu Stir-fry"},
    )
    assert isinstance(result, Recipe)


def test_refinement_preserves_all_fields() -> None:
    result = _run("make it vegan", _BASE_RECIPE.model_dump())
    assert result.name != ""
    assert len(result.steps) >= 1
    assert len(result.ingredients) >= 1
    assert result.difficulty in ("Easy", "Medium", "Hard")
    assert result.prep_time_minutes > 0
    assert result.cook_time_minutes > 0
    assert result.servings > 0


def test_refinement_can_change_difficulty() -> None:
    harder = {**_BASE_RECIPE.model_dump(), "difficulty": "Hard", "steps": ["Complex step 1.", "Complex step 2.", "Complex step 3.", "Complex step 4."]}
    result = _run("make it more challenging", harder)
    assert result.difficulty in ("Easy", "Medium", "Hard")


def test_refinement_can_reduce_cook_time() -> None:
    faster = {**_BASE_RECIPE.model_dump(), "cook_time_minutes": 5}
    result = _run("make it faster", faster)
    assert result.cook_time_minutes > 0


def test_refinement_prompt_contains_recipe_json() -> None:
    prompt = refinement_prompt(_BASE_RECIPE, "make it vegan")
    assert "Chicken Stir-fry" in prompt
    assert "make it vegan" in prompt


def test_refinement_result_is_pydantic_valid() -> None:
    result = _run("add more steps", _BASE_RECIPE.model_dump())
    dumped = result.model_dump()
    reparsed = Recipe.model_validate(dumped)
    assert reparsed.name == result.name
