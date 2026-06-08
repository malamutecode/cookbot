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
    CalendarAddEvent,
    CalendarRemoveEvent,
    ChatAgentDeps,
    FinalRecipeEvent,
    OnboardingState,
    RecipeOptionsEvent,
    ShoppingListEvent,
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


def _events_of(deps, event_cls):
    """All deps.events of a given event type, in order."""
    return [ev for ev in deps.events if isinstance(ev, event_cls)]


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

def test_reset_turn_clears_events_and_preserves_durable() -> None:
    from cookbot.agents.chat import FoundRecipe, RecipeOptionsEvent
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
        # per-turn output events (should be wiped)
        events=[RecipeOptionsEvent(proposals=[proposal])],
    )

    deps.reset_turn()

    # Per-turn output events cleared
    assert deps.events == []

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
    adds = _events_of(deps, CalendarAddEvent)
    assert len(adds) == 1
    assert adds[0].entry.recipe_name == "Tomato Pasta"
    assert adds[0].entry.date == "2026-06-01"
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
    assert _events_of(deps, CalendarRemoveEvent) == []


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
    assert [ev.entry_id for ev in _events_of(deps, CalendarRemoveEvent)] == ["abc"]


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
    sl_events = _events_of(deps, ShoppingListEvent)
    assert len(sl_events) == 1
    assert sl_events[0].shopping_list == fake_list


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
    sl_events = _events_of(deps, ShoppingListEvent)
    assert len(sl_events) == 1
    assert sl_events[0].shopping_list.items == []


# ── _normalize_date ───────────────────────────────────────────────────────────

import datetime as _dt  # noqa: E402


def test_normalize_date_pads_iso() -> None:
    from cookbot.agents.chat import _normalize_date
    assert _normalize_date("2026-06-4") == "2026-06-04"
    assert _normalize_date("2026-6-4") == "2026-06-04"
    assert _normalize_date("2026/06/04") == "2026-06-04"


def test_normalize_date_day_first_with_year() -> None:
    from cookbot.agents.chat import _normalize_date
    assert _normalize_date("4.06.2026") == "2026-06-04"
    assert _normalize_date("04/6/2026") == "2026-06-04"


def test_normalize_date_year_less_assumes_current_year() -> None:
    from cookbot.agents.chat import _normalize_date
    year = _dt.date.today().year
    assert _normalize_date("4.06") == f"{year}-06-04"


def test_normalize_date_already_iso_unchanged() -> None:
    from cookbot.agents.chat import _normalize_date
    year = _dt.date.today().year
    assert _normalize_date(f"{year}-08-10") == f"{year}-08-10"


def test_normalize_date_bumps_past_year_to_current() -> None:
    # Regression: the agent emitted "2023-06-05" while today is 2026 → the entry
    # landed years out of the visible calendar week and never rendered.
    from cookbot.agents.chat import _normalize_date
    year = _dt.date.today().year
    assert _normalize_date("2023-06-05") == f"{year}-06-05"
    assert _normalize_date("5.6.2022") == f"{year}-06-05"


def test_normalize_date_keeps_future_year() -> None:
    from cookbot.agents.chat import _normalize_date
    year = _dt.date.today().year
    future = year + 2
    assert _normalize_date(f"{future}-01-15") == f"{future}-01-15"


def test_normalize_date_passthrough_when_unparseable() -> None:
    from cookbot.agents.chat import _normalize_date
    assert _normalize_date("jutro") == "jutro"


async def test_add_to_calendar_normalizes_entry_date() -> None:
    deps = _make_deps()
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "add_to_calendar")
    ctx = MagicMock()
    ctx.deps = deps

    # Unpadded date from the LLM must become a strict ISO string on the entry.
    result = await fn(ctx, recipe_name="Pasta", ingredients=["x"], target_date="2026-06-4")
    assert result.date == "2026-06-04"
    adds = _events_of(deps, CalendarAddEvent)
    assert adds[0].entry.date == "2026-06-04"


# ── _select_proposal (pure selection logic) ───────────────────────────────────

def _summary(name: str, source: str = "ai_generated", url: str | None = None) -> "RecipeSummary":
    from cookbot.models.recipe import RecipeSummary
    return RecipeSummary(
        name=name, description="d", difficulty="Easy", total_time_minutes=20,
        key_ingredients=["x"], source=source, source_url=url,
    )


def test_select_proposal_by_number() -> None:
    from cookbot.agents.chat import _select_proposal
    props = [_summary("A"), _summary("B"), _summary("C")]
    assert _select_proposal(props, "2") is props[1]


def test_select_proposal_by_name_substring() -> None:
    from cookbot.agents.chat import _select_proposal
    props = [_summary("Tomato Pasta"), _summary("Chicken Soup")]
    assert _select_proposal(props, "soup") is props[1]


def test_select_proposal_falls_back_to_first() -> None:
    from cookbot.agents.chat import _select_proposal
    props = [_summary("A"), _summary("B")]
    assert _select_proposal(props, "nonsense") is props[0]


def test_select_proposal_none_when_empty() -> None:
    from cookbot.agents.chat import _select_proposal
    assert _select_proposal([], "1") is None


# ── resolve_recipe (extracted decision tree) ──────────────────────────────────

def _stub_agent_factory(output):
    """build_*_agent replacement: returns an agent whose run() yields `output`,
    recording that it was called."""
    calls: list[int] = []

    class _Stub:
        async def run(self, *_a, **_k):  # noqa: ANN202
            calls.append(1)
            return MagicMock(output=output)

    return (lambda _config: _Stub()), calls


async def test_resolve_recipe_fetches_known_url() -> None:
    from cookbot.agents.chat import resolve_recipe
    selected = _summary("Pasta", source="web_search", url="https://x.test/pasta")
    fetch_factory, fetch_calls = _stub_agent_factory(_RECIPE)

    with patch("cookbot.agents.chat.build_web_fetch_agent", fetch_factory):
        found = await resolve_recipe(
            selected, "1", OnboardingState(servings=2),
            config=_CONFIG, site_filter="", allow_ai_generated=True,
        )

    assert found.source == "web_search"
    assert found.recipe.name == "Test Pasta"
    assert len(fetch_calls) == 1  # fetched the known URL, no second search


async def test_resolve_recipe_searches_by_name_when_no_url() -> None:
    from cookbot.agents.chat import resolve_recipe
    selected = _summary("Pasta", source="web_search", url=None)
    search_factory, search_calls = _stub_agent_factory(_RECIPE)

    with patch("cookbot.agents.chat.build_web_search_agent", search_factory):
        found = await resolve_recipe(
            selected, "1", OnboardingState(servings=2),
            config=_CONFIG, site_filter="", allow_ai_generated=True,
        )

    assert found.source == "web_search"
    assert len(search_calls) == 1


async def test_resolve_recipe_gen_fallback_when_search_empty() -> None:
    from cookbot.agents.chat import resolve_recipe
    selected = _summary("Pasta", source="web_search", url="https://x.test/p")
    fetch_factory, _ = _stub_agent_factory(None)         # fetch finds nothing
    search_factory, _ = _stub_agent_factory(None)        # web search also empty
    gen_factory, gen_calls = _stub_agent_factory(_RECIPE)  # gen produces a recipe

    with patch("cookbot.agents.chat.build_web_fetch_agent", fetch_factory), \
         patch("cookbot.agents.chat.build_web_search_agent", search_factory), \
         patch("cookbot.agents.chat.build_recipe_gen_agent", gen_factory):
        found = await resolve_recipe(
            selected, "1", OnboardingState(servings=2),
            config=_CONFIG, site_filter="", allow_ai_generated=True,
        )

    assert found.source == "ai_generated"
    assert len(gen_calls) == 1
    # User picked a WEB option but we couldn't read it → flag set so the agent
    # tells the user the recipe was AI-generated.
    assert found.web_pick_fell_back is True


async def test_resolve_recipe_not_found_when_ai_disabled() -> None:
    from cookbot.agents.chat import resolve_recipe
    selected = _summary("Pasta", source="web_search", url="https://x.test/p")
    fetch_factory, _ = _stub_agent_factory(None)   # fetch finds nothing
    search_factory, _ = _stub_agent_factory(None)  # web search also empty

    def _gen_boom(_config):  # noqa: ANN202
        raise AssertionError("RecipeGenAgent must not run when AI is disabled")

    with patch("cookbot.agents.chat.build_web_fetch_agent", fetch_factory), \
         patch("cookbot.agents.chat.build_web_search_agent", search_factory), \
         patch("cookbot.agents.chat.build_recipe_gen_agent", _gen_boom):
        found = await resolve_recipe(
            selected, "1", OnboardingState(servings=4),
            config=_CONFIG, site_filter="", allow_ai_generated=False,
        )

    assert found.source == "not_found"
    assert found.recipe.servings == 4
    assert found.recipe.steps == []


async def test_resolve_recipe_known_url_fetch_retries_then_succeeds() -> None:
    # Extraction is intermittent — the known-URL fetch retries once, and a
    # success on the 2nd attempt keeps the user on THEIR chosen page.
    from cookbot.agents.chat import resolve_recipe
    selected = _summary("Makaron ze szpinakiem", source="web_search",
                        url="https://kwestiasmaku.com/pasta/x/przepis.html")
    web_recipe = _RECIPE.model_copy(update={"source_url": selected.source_url})

    calls: list[int] = []

    class _FlakyFetch:
        async def run(self, *_a, **_k):  # noqa: ANN202
            calls.append(1)
            return MagicMock(output=None if len(calls) == 1 else web_recipe)

    def _gen_boom(_config):  # noqa: ANN202
        raise AssertionError("must not AI-generate when the retry succeeds")

    with patch("cookbot.agents.chat.build_web_fetch_agent", lambda _c: _FlakyFetch()), \
         patch("cookbot.agents.chat.build_recipe_gen_agent", _gen_boom):
        found = await resolve_recipe(
            selected, "1", OnboardingState(servings=2),
            config=_CONFIG, site_filter="", allow_ai_generated=True,
        )

    assert len(calls) == 2                 # retried once
    assert found.source == "web_search"
    assert found.recipe.source_url == selected.source_url


async def test_resolve_recipe_known_url_fail_does_not_wander_to_other_site() -> None:
    # When a SPECIFIC URL was picked and both fetch attempts fail, we must NOT
    # run a name-search (which could return a recipe from a different site and
    # mis-attribute it). Fall back to AI, flagged.
    from cookbot.agents.chat import resolve_recipe
    selected = _summary("Makaron", source="web_search",
                        url="https://kwestiasmaku.com/pasta/x/przepis.html")
    fetch_factory, fetch_calls = _stub_agent_factory(None)   # always fails
    gen_factory, gen_calls = _stub_agent_factory(_RECIPE)

    def _search_boom(_config):  # noqa: ANN202
        raise AssertionError("must NOT name-search when a specific URL was picked")

    with patch("cookbot.agents.chat.build_web_fetch_agent", fetch_factory), \
         patch("cookbot.agents.chat.build_web_search_agent", _search_boom), \
         patch("cookbot.agents.chat.build_recipe_gen_agent", gen_factory):
        found = await resolve_recipe(
            selected, "1", OnboardingState(servings=2),
            config=_CONFIG, site_filter="", allow_ai_generated=True,
        )

    assert len(fetch_calls) == 2           # two attempts on the picked URL
    assert len(gen_calls) == 1             # then AI, not a wandering search
    assert found.source == "ai_generated"
    assert found.web_pick_fell_back is True


async def test_resolve_recipe_backfills_source_url_from_proposal() -> None:
    # Fetch succeeds but the extractor omitted source_url → backfill from the
    # proposal so a web recipe always keeps its provenance link.
    from cookbot.agents.chat import resolve_recipe
    selected = _summary("Pasta", source="web_search", url="https://x.test/pasta")
    recipe_no_url = _RECIPE.model_copy(update={"source_url": None})
    fetch_factory, _ = _stub_agent_factory(recipe_no_url)

    with patch("cookbot.agents.chat.build_web_fetch_agent", fetch_factory):
        found = await resolve_recipe(
            selected, "1", OnboardingState(servings=2),
            config=_CONFIG, site_filter="", allow_ai_generated=True,
        )

    assert found.source == "web_search"
    assert found.recipe.source_url == "https://x.test/pasta"


async def test_resolve_recipe_ai_proposal_generates_directly() -> None:
    from cookbot.agents.chat import resolve_recipe
    selected = _summary("Invented Dish", source="ai_generated")
    gen_factory, gen_calls = _stub_agent_factory(_RECIPE)

    with patch("cookbot.agents.chat.build_recipe_gen_agent", gen_factory):
        found = await resolve_recipe(
            selected, "1", OnboardingState(servings=2),
            config=_CONFIG, site_filter="", allow_ai_generated=True,
        )

    assert found.source == "ai_generated"
    assert len(gen_calls) == 1
    # Picking an AI proposal is NOT a "web pick that fell back" — no note needed.
    assert found.web_pick_fell_back is False


# ── Turn events: ordering and emission gating ─────────────────────────────────

async def test_get_recipe_details_appends_final_recipe_event() -> None:
    deps = _make_deps(last_proposals=[_summary("Pasta", source="ai_generated")])
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "get_recipe_details")
    ctx = MagicMock()
    ctx.deps = deps

    gen_factory, _ = _stub_agent_factory(_RECIPE)
    with patch("cookbot.agents.chat.build_recipe_gen_agent", gen_factory):
        await fn(ctx, choice="1")

    finals = _events_of(deps, FinalRecipeEvent)
    assert len(finals) == 1
    assert finals[0].source == "ai_generated"
    assert finals[0].recipe.name == "Test Pasta"


async def test_not_found_emits_no_final_recipe_event() -> None:
    # web_search proposal, fetch finds nothing, AI disabled → not_found → no event
    deps = _make_deps(
        last_proposals=[_summary("Pasta", source="web_search", url="https://x.test/p")],
        allow_ai_generated=False,
    )
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "get_recipe_details")
    ctx = MagicMock()
    ctx.deps = deps

    fetch_factory, _ = _stub_agent_factory(None)
    search_factory, _ = _stub_agent_factory(None)  # web search also empty
    with patch("cookbot.agents.chat.build_web_fetch_agent", fetch_factory), \
         patch("cookbot.agents.chat.build_web_search_agent", search_factory):
        found = await fn(ctx, choice="1")

    assert found.source == "not_found"
    assert _events_of(deps, FinalRecipeEvent) == []   # placeholder stays silent
    assert deps.last_recipe is not None               # still recorded for the agent


async def test_events_preserve_tool_call_order() -> None:
    # A turn that delivers a recipe then adds it to the calendar should produce
    # events in call order: FinalRecipeEvent before CalendarAddEvent.
    deps = _make_deps(last_proposals=[_summary("Pasta", source="ai_generated")])
    agent = build_chat_agent(_CONFIG)
    get_details = _get_tool(agent, "get_recipe_details")
    add_cal = _get_tool(agent, "add_to_calendar")
    ctx = MagicMock()
    ctx.deps = deps

    gen_factory, _ = _stub_agent_factory(_RECIPE)
    with patch("cookbot.agents.chat.build_recipe_gen_agent", gen_factory):
        await get_details(ctx, choice="1")
    await add_cal(ctx, recipe_name="Test Pasta",
                  ingredients=["pasta"], target_date="2026-06-01")

    kinds = [ev.kind for ev in deps.events]
    assert kinds == ["final_recipe", "calendar_add"]


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
