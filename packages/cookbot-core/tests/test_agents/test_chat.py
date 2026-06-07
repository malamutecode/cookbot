"""
Tests for the guided chat agent — onboarding state and tool logic.

Tool functions are tested by calling the agent's registered tool functions
directly (via agent._function_tools dict).  No real LLM calls are made.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from cookbot.agents.chat import (
    ChatAgentDeps,
    OnboardingState,
    build_chat_agent,
    stream_chat_response,
)
from cookbot.models.calendar import CalendarEntry, CalendarState
from cookbot.models.recipe import Recipe
from cookbot.models.shopping import ShoppingItem, ShoppingList
from cookbot.models.tenant import TenantConfig

_CONFIG = TenantConfig(
    tenant_id="test",
    persona="You are a helpful chef",
    language="en",
    recipe_source_url="",
    allowed_origins=[],
)

_RECIPE = Recipe(
    name="Test Pasta",
    description="Simple test pasta.",
    ingredients=["200g pasta", "2 tomatoes"],
    steps=["Boil pasta.", "Make sauce.", "Combine."],
    prep_time_minutes=5,
    cook_time_minutes=20,
    difficulty="Easy",
    servings=2,
    tips=[],
)


def _make_deps(calendar: CalendarState | None = None, **kwargs) -> ChatAgentDeps:
    return ChatAgentDeps(
        config=_CONFIG,
        calendar=calendar or CalendarState(),
        **kwargs,
    )


def _get_tool(agent, name):
    """Return the raw async function registered under a tool name."""
    return agent._function_toolset.tools[name].function


# ── OnboardingState unit tests ────────────────────────────────────────────────

def test_onboarding_state_starts_incomplete() -> None:
    assert not OnboardingState().complete


def test_onboarding_state_complete_when_all_set() -> None:
    ob = OnboardingState(
        dish_type="pasta", servings=2, max_time_minutes=30,
        ingredients=["tomato"], free_notes="",
    )
    assert ob.complete


def test_onboarding_next_missing_field_order() -> None:
    ob = OnboardingState()
    assert ob.next_missing_field() == "dish_type"
    ob.dish_type = "pasta"
    assert ob.next_missing_field() == "servings"
    ob.servings = 2
    assert ob.next_missing_field() == "max_time_minutes"
    ob.max_time_minutes = 0
    assert ob.next_missing_field() == "ingredients"
    ob.ingredients = []
    assert ob.next_missing_field() == "free_notes"
    ob.free_notes = ""
    assert ob.next_missing_field() is None


def test_onboarding_to_intent_defaults() -> None:
    intent = OnboardingState().to_intent()
    assert intent.dish_type == "any"
    assert intent.servings == 2
    assert intent.max_time_minutes == 0
    assert intent.available_ingredients == []
    assert intent.free_notes == ""


def test_onboarding_to_intent_uses_set_values() -> None:
    ob = OnboardingState(dish_type="soup", servings=4, max_time_minutes=45,
                         ingredients=["carrot", "celery"], free_notes="no salt")
    intent = ob.to_intent()
    assert intent.dish_type == "soup"
    assert intent.servings == 4
    assert intent.max_time_minutes == 45
    assert intent.available_ingredients == ["carrot", "celery"]
    assert intent.free_notes == "no salt"


# ── Agent build ───────────────────────────────────────────────────────────────

def test_build_chat_agent_returns_agent() -> None:
    assert build_chat_agent(_CONFIG) is not None


def test_agent_registers_expected_tools() -> None:
    agent = build_chat_agent(_CONFIG)
    assert set(agent._function_toolset.tools.keys()) == {
        "update_onboarding",
        "propose_recipes",
        "get_recipe_details",
        "add_to_calendar",
        "remove_from_calendar",
        "get_shopping_list",
    }


# ── ChatAgentDeps.reset_turn ──────────────────────────────────────────────────

def test_reset_turn_clears_collectors_and_preserves_durable() -> None:
    from cookbot.agents.chat import FoundRecipe
    from cookbot.models.recipe import RecipeSummary

    proposal = RecipeSummary(
        name="Pasta", description="d", difficulty="Easy",
        total_time_minutes=20, key_ingredients=["pasta"], source="ai_generated",
    )
    deps = _make_deps(
        # durable fields
        onboarding=OnboardingState(dish_type="pasta", servings=2),
        last_recipe=FoundRecipe(recipe=_RECIPE, source="ai_generated"),
        last_proposals=[proposal],
        # per-turn input
        search_site_filter="site:example.com",
        allow_ai_generated=False,
        # per-turn output collectors (should all be wiped)
        recipe_ready_this_turn=True,
        calendar_adds=[CalendarEntry(id="1", date="2026-06-01",
                                     recipe_name="X", ingredients=["a"])],
        calendar_removes=["zzz"],
        shopping_list_items=ShoppingList(items=[], sections=[]),
        recipe_options=[proposal],
    )

    deps.reset_turn()

    # Per-turn output collectors cleared
    assert deps.recipe_ready_this_turn is False
    assert deps.calendar_adds == []
    assert deps.calendar_removes == []
    assert deps.shopping_list_items is None
    assert deps.recipe_options == []

    # Connection-durable fields untouched
    assert deps.onboarding.dish_type == "pasta"
    assert deps.onboarding.servings == 2
    assert deps.last_recipe is not None
    assert deps.last_proposals == [proposal]

    # Per-turn input fields untouched (handler refreshes these, not reset_turn)
    assert deps.search_site_filter == "site:example.com"
    assert deps.allow_ai_generated is False


# ── update_onboarding tool ────────────────────────────────────────────────────

async def test_update_onboarding_sets_fields() -> None:
    deps = _make_deps()
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "update_onboarding")

    ctx = MagicMock()
    ctx.deps = deps

    result = await fn(ctx, dish_type="pasta", servings=2)
    assert deps.onboarding.dish_type == "pasta"
    assert deps.onboarding.servings == 2
    assert result["complete"] is False
    assert result["next_missing_field"] == "max_time_minutes"


async def test_update_onboarding_complete_when_all_set() -> None:
    deps = _make_deps(onboarding=OnboardingState(
        dish_type="pasta", servings=2, max_time_minutes=30,
        ingredients=["tomato"], free_notes="",
    ))
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "update_onboarding")

    ctx = MagicMock()
    ctx.deps = deps

    result = await fn(ctx)
    assert result["complete"] is True
    assert result["next_missing_field"] is None


async def test_update_onboarding_partial_update_preserves_existing() -> None:
    deps = _make_deps(onboarding=OnboardingState(dish_type="soup"))
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "update_onboarding")

    ctx = MagicMock()
    ctx.deps = deps

    await fn(ctx, servings=3)
    assert deps.onboarding.dish_type == "soup"   # unchanged
    assert deps.onboarding.servings == 3          # new


# ── add_to_calendar tool ──────────────────────────────────────────────────────

async def test_add_to_calendar_appends_entry() -> None:
    deps = _make_deps()
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "add_to_calendar")

    ctx = MagicMock()
    ctx.deps = deps

    result = await fn(ctx, recipe_name="Tomato Pasta",
                      ingredients=["pasta", "tomato"], target_date="2026-06-01")
    assert len(deps.calendar_adds) == 1
    assert deps.calendar_adds[0].recipe_name == "Tomato Pasta"
    assert deps.calendar_adds[0].date == "2026-06-01"
    assert result.recipe_name == "Tomato Pasta"


# ── remove_from_calendar tool ─────────────────────────────────────────────────

async def test_remove_from_calendar_unknown_id_returns_false() -> None:
    deps = _make_deps()
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "remove_from_calendar")

    ctx = MagicMock()
    ctx.deps = deps

    result = await fn(ctx, entry_id="nonexistent")
    assert result.removed is False
    assert deps.calendar_removes == []


async def test_remove_from_calendar_known_id_returns_true() -> None:
    calendar = CalendarState(entries=[
        CalendarEntry(id="abc", date="2026-06-01",
                      recipe_name="Pasta", ingredients=["pasta"]),
    ])
    deps = _make_deps(calendar=calendar)
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "remove_from_calendar")

    ctx = MagicMock()
    ctx.deps = deps

    result = await fn(ctx, entry_id="abc")
    assert result.removed is True
    assert "abc" in deps.calendar_removes


# ── get_shopping_list tool ────────────────────────────────────────────────────

def _stub_shopping_agent(shopping_list: ShoppingList):
    """Return a fake build_shopping_list_agent that ignores input and yields
    a fixed ShoppingList, capturing the raw text it was called with."""
    captured: dict[str, str] = {}

    class _StubAgent:
        async def run(self, raw_text: str):  # noqa: ANN202
            captured["raw_text"] = raw_text
            return MagicMock(output=shopping_list)

    return (lambda _config: _StubAgent()), captured


async def test_get_shopping_list_filters_by_date_range() -> None:
    # The tool's own responsibility is date-range filtering + delegating the
    # raw ingredient strings to ShoppingListAgent (dedup/sectioning is the
    # agent's job, mocked here).
    calendar = CalendarState(entries=[
        CalendarEntry(id="1", date="2026-06-01", recipe_name="Pasta",
                      ingredients=["pasta", "tomato", "garlic"]),
        CalendarEntry(id="2", date="2026-06-02", recipe_name="Soup",
                      ingredients=["carrot", "tomato"]),
        CalendarEntry(id="3", date="2026-06-10", recipe_name="Out of range",
                      ingredients=["chicken"]),
    ])
    deps = _make_deps(calendar=calendar)
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "get_shopping_list")

    ctx = MagicMock()
    ctx.deps = deps

    fake_list = ShoppingList(
        items=[
            ShoppingItem(name="pasta", quantity="200g", section="suche produkty"),
            ShoppingItem(name="tomato", quantity="3 szt.", section="warzywa/owoce"),
        ],
        sections=["warzywa/owoce", "suche produkty"],
    )
    stub_factory, captured = _stub_shopping_agent(fake_list)
    with patch("cookbot.agents.chat.build_shopping_list_agent", stub_factory):
        result = await fn(ctx, date_from="2026-06-01", date_to="2026-06-05")

    # Only in-range ingredients reach the agent; "chicken" (2026-06-10) excluded.
    assert "chicken" not in captured["raw_text"]
    assert "pasta" in captured["raw_text"]
    assert "carrot" in captured["raw_text"]
    # Result reflects the (mocked) structured list.
    assert result.item_count == 2
    assert result.sections == ["warzywa/owoce", "suche produkty"]
    assert result.date_from == "2026-06-01"
    assert result.date_to == "2026-06-05"
    assert deps.shopping_list_items == fake_list


async def test_get_shopping_list_empty_range_skips_agent() -> None:
    deps = _make_deps(calendar=CalendarState(entries=[
        CalendarEntry(id="1", date="2026-07-01", recipe_name="Future",
                      ingredients=["x"]),
    ]))
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "get_shopping_list")

    ctx = MagicMock()
    ctx.deps = deps

    # No in-range entries → the agent must NOT be called; empty list returned.
    def _boom(_config):  # noqa: ANN202
        raise AssertionError("ShoppingListAgent should not run for an empty range")

    with patch("cookbot.agents.chat.build_shopping_list_agent", _boom):
        result = await fn(ctx, date_from="2026-06-01", date_to="2026-06-30")

    assert result.item_count == 0
    assert result.sections == []
    assert deps.shopping_list_items is not None
    assert deps.shopping_list_items.items == []


# ── stream_chat_response (integration, no tool calls) ────────────────────────

async def test_stream_chat_response_yields_tokens() -> None:
    deps = _make_deps()
    agent = build_chat_agent(_CONFIG)
    history: list = []
    tokens: list[str] = []
    with agent.override(model=TestModel(custom_output_text="Just a greeting.", call_tools=[])):
        async with stream_chat_response(agent, deps, history, "Hello") as token_iter:
            async for token in token_iter:
                tokens.append(token)
    assert len(tokens) > 0
    assert all(isinstance(t, str) for t in tokens)
    # history should be updated after the block
    assert len(history) > 0
