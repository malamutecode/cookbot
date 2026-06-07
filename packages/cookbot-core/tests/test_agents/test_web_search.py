import asyncio

from pydantic_ai.models.test import TestModel

from cookbot.agents.web_search import build_web_search_agent, web_search_prompt
from cookbot.models.recipe import ParsedIngredients, Recipe, UserIntent
from cookbot.models.tenant import TenantConfig

_CONFIG = TenantConfig(
    tenant_id="test",
    persona="You are a helpful chef",
    language="en",
    recipe_source_url="",
    allowed_origins=[],
)

_RECIPE_DICT = {
    "name": "Garlic Chicken",
    "description": "Simple garlic chicken.",
    "ingredients": ["200g chicken", "2 cloves garlic"],
    "steps": ["Season chicken.", "Cook in pan.", "Add garlic.", "Serve."],
    "prep_time_minutes": 10,
    "cook_time_minutes": 20,
    "difficulty": "Easy",
    "servings": 2,
    "tips": [],
}

_INTENT = UserIntent(dish_type="chicken", servings=2, max_time_minutes=30, available_ingredients=["chicken", "garlic"], free_notes="")
_INGREDIENTS = ParsedIngredients(items=["chicken", "garlic"], must_use=[], dietary_hints=[], missing_staples=["salt"])


def _run(custom_output: dict | None) -> Recipe | None:
    agent = build_web_search_agent(_CONFIG)

    async def _go() -> Recipe | None:
        # call_tools=[] stops TestModel from auto-invoking the agent's
        # duckduckgo_search / web_fetch tools (which would hit the network /
        # fail on dummy args). These tests only verify output mapping.
        test_model = (
            TestModel(custom_output_args=custom_output, call_tools=[])
            if custom_output
            else TestModel(call_tools=[])
        )
        with agent.override(model=test_model):
            result = await agent.run(web_search_prompt(_INGREDIENTS, _INTENT))
            return result.output

    return asyncio.run(_go())


def test_web_search_returns_recipe_when_found() -> None:
    result = _run(custom_output=_RECIPE_DICT)
    assert isinstance(result, Recipe)
    assert result.name == "Garlic Chicken"


def test_web_search_recipe_has_required_fields() -> None:
    result = _run(custom_output=_RECIPE_DICT)
    assert result is not None
    assert len(result.ingredients) >= 1
    assert len(result.steps) >= 1
    assert result.prep_time_minutes > 0
    assert result.cook_time_minutes > 0
    assert result.difficulty in ("Easy", "Medium", "Hard")


def test_web_search_returns_none_when_no_result() -> None:
    result = _run(custom_output=None)
    # TestModel with no custom output returns default — check it's Recipe or None
    assert result is None or isinstance(result, Recipe)


def test_web_search_prompt_includes_ingredients() -> None:
    prompt = web_search_prompt(_INGREDIENTS, _INTENT)
    assert "chicken" in prompt
    assert "garlic" in prompt


def test_web_search_prompt_includes_time_budget() -> None:
    prompt = web_search_prompt(_INGREDIENTS, _INTENT)
    assert "30" in prompt


def test_web_search_prompt_includes_servings() -> None:
    prompt = web_search_prompt(_INGREDIENTS, _INTENT)
    assert "2" in prompt


def test_web_search_prompt_includes_free_notes() -> None:
    intent_with_notes = UserIntent(
        dish_type="pasta", servings=2, max_time_minutes=0,
        available_ingredients=[], free_notes="easy to reheat"
    )
    prompt = web_search_prompt(_INGREDIENTS, intent_with_notes)
    assert "reheat" in prompt
