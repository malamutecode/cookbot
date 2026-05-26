import asyncio

from pydantic_ai.models.test import TestModel

from cookbot.agents.ingredient import build_ingredient_agent, intent_to_prompt
from cookbot.models.recipe import ParsedIngredients, UserIntent
from cookbot.models.tenant import TenantConfig

_CONFIG = TenantConfig(
    tenant_id="test",
    persona="You are a helpful chef",
    language="en",
    recipe_source_url="",
    allowed_origins=[],
)


def _run(intent: UserIntent, custom_output: dict | None = None) -> ParsedIngredients:
    agent = build_ingredient_agent(_CONFIG)

    async def _go() -> ParsedIngredients:
        test_model = TestModel(custom_output_args=custom_output) if custom_output else TestModel()
        with agent.override(model=test_model):
            result = await agent.run(intent_to_prompt(intent))
            return result.output

    return asyncio.run(_go())


def test_ingredient_returns_parsed_ingredients_type() -> None:
    intent = UserIntent(dish_type="pasta", servings=2, max_time_minutes=30, available_ingredients=["chicken", "spinach", "garlic"], free_notes="")
    result = _run(
        intent,
        custom_output={"items": ["chicken", "spinach", "garlic"], "must_use": [], "dietary_hints": [], "missing_staples": ["salt", "olive oil"]},
    )
    assert isinstance(result, ParsedIngredients)


def test_ingredient_items_contain_all_provided() -> None:
    intent = UserIntent(dish_type="stir-fry", servings=2, max_time_minutes=20, available_ingredients=["chicken", "spinach", "garlic"], free_notes="")
    result = _run(
        intent,
        custom_output={"items": ["chicken", "spinach", "garlic"], "must_use": [], "dietary_hints": [], "missing_staples": []},
    )
    assert "chicken" in result.items
    assert "spinach" in result.items
    assert "garlic" in result.items


def test_ingredient_dietary_hint_extracted() -> None:
    intent = UserIntent(dish_type="vegan pasta", servings=2, max_time_minutes=30, available_ingredients=["spinach"], free_notes="")
    result = _run(
        intent,
        custom_output={"items": ["spinach"], "must_use": [], "dietary_hints": ["vegan"], "missing_staples": []},
    )
    assert "vegan" in result.dietary_hints


def test_ingredient_empty_input_returns_empty_lists() -> None:
    intent = UserIntent(dish_type="any", servings=0, max_time_minutes=0, available_ingredients=[], free_notes="")
    result = _run(
        intent,
        custom_output={"items": [], "must_use": [], "dietary_hints": [], "missing_staples": []},
    )
    assert result.items == []
    assert result.must_use == []


def test_ingredient_single_item_works() -> None:
    intent = UserIntent(dish_type="any", servings=1, max_time_minutes=0, available_ingredients=["egg"], free_notes="")
    result = _run(
        intent,
        custom_output={"items": ["egg"], "must_use": [], "dietary_hints": [], "missing_staples": ["salt"]},
    )
    assert "egg" in result.items


def test_intent_to_prompt_contains_all_fields() -> None:
    intent = UserIntent(dish_type="soup", servings=4, max_time_minutes=45, available_ingredients=["carrot", "onion"], free_notes="easy to reheat")
    prompt = intent_to_prompt(intent)
    assert "soup" in prompt
    assert "4" in prompt    # servings
    assert "45" in prompt   # time
    assert "carrot" in prompt
    assert "onion" in prompt


def test_intent_to_prompt_zero_time_says_no_limit() -> None:
    intent = UserIntent(dish_type="any", servings=0, max_time_minutes=0, available_ingredients=[], free_notes="")
    prompt = intent_to_prompt(intent)
    assert "no time limit" in prompt
