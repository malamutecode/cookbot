import pytest
from pydantic_ai.models.test import TestModel

from cookbot.agents.intake import build_intake_agent
from cookbot.models.recipe import UserIntent
from cookbot.models.tenant import TenantConfig

_CONFIG = TenantConfig(
    tenant_id="test",
    persona="You are a helpful chef",
    language="en",
    recipe_source_url="",
    allowed_origins=[],
)


def _run(prompt: str, custom_output: dict | None = None) -> UserIntent:
    import asyncio

    agent = build_intake_agent(_CONFIG)

    async def _go() -> UserIntent:
        test_model = TestModel(custom_output_args=custom_output) if custom_output else TestModel()
        with agent.override(model=test_model):
            result = await agent.run(prompt)
            return result.output

    return asyncio.run(_go())


def test_intake_questions_list_has_five_items() -> None:
    assert len(_CONFIG.ui.intake_questions) == 5


def test_intake_returns_user_intent_type() -> None:
    result = _run(
        "Q: dish?\nA: pasta\nQ: time?\nA: 30 min\nQ: ingredients?\nA: spinach, garlic",
        custom_output={"dish_type": "pasta", "servings": 2, "max_time_minutes": 30, "available_ingredients": ["spinach", "garlic"], "free_notes": ""},
    )
    assert isinstance(result, UserIntent)


def test_intake_dish_type_captured() -> None:
    result = _run(
        "pasta",
        custom_output={"dish_type": "pasta", "servings": 0, "max_time_minutes": 0, "available_ingredients": [], "free_notes": ""},
    )
    assert result.dish_type == "pasta"


def test_intake_no_time_constraint_gives_zero() -> None:
    result = _run(
        "no rush",
        custom_output={"dish_type": "any", "servings": 0, "max_time_minutes": 0, "available_ingredients": [], "free_notes": ""},
    )
    assert result.max_time_minutes == 0


def test_intake_ingredients_captured() -> None:
    result = _run(
        "I have spinach and garlic",
        custom_output={"dish_type": "any", "servings": 0, "max_time_minutes": 0, "available_ingredients": ["spinach", "garlic"], "free_notes": ""},
    )
    assert "spinach" in result.available_ingredients
    assert "garlic" in result.available_ingredients


def test_intake_no_ingredients_gives_empty_list() -> None:
    result = _run(
        "no ingredients",
        custom_output={"dish_type": "any", "servings": 0, "max_time_minutes": 0, "available_ingredients": [], "free_notes": ""},
    )
    assert result.available_ingredients == []


def test_intake_free_notes_captured() -> None:
    result = _run(
        "easy to reheat at the office",
        custom_output={"dish_type": "any", "servings": 1, "max_time_minutes": 0, "available_ingredients": [], "free_notes": "easy to reheat at the office"},
    )
    assert "reheat" in result.free_notes


def test_intake_no_notes_gives_empty_string() -> None:
    result = _run(
        "no",
        custom_output={"dish_type": "any", "servings": 0, "max_time_minutes": 0, "available_ingredients": [], "free_notes": ""},
    )
    assert result.free_notes == ""
