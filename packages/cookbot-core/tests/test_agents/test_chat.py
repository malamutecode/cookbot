"""
Tests for the guided chat agent — onboarding state and tool logic.

Tool functions are tested by calling the agent's registered tool functions
directly (via agent._function_tools dict).  No real LLM calls are made.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
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
    onboarding_status_prompt,
    stream_chat_response,
)
from cookbot.models.calendar import CalendarEntry, CalendarState, MealSlot
from cookbot.models.recipe import Recipe, RecipeSummary
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


def test_has_concrete_dish_true_for_named_dish() -> None:
    assert OnboardingState(dish_type="halloumi").has_concrete_dish()
    assert OnboardingState(dish_type="Makaron Carbonara").has_concrete_dish()


def test_has_concrete_dish_false_for_any_or_none() -> None:
    assert not OnboardingState().has_concrete_dish()          # not answered
    assert not OnboardingState(dish_type="any").has_concrete_dish()  # "zaproponuj coś"
    assert not OnboardingState(dish_type="  ANY ").has_concrete_dish()
    assert not OnboardingState(dish_type="").has_concrete_dish()


def test_ready_to_search_on_concrete_dish_without_full_onboarding() -> None:
    # A named dish is enough to search, even though servings/time/etc. are unset.
    ob = OnboardingState(dish_type="halloumi", servings=2)
    assert not ob.complete
    assert ob.ready_to_search()


def test_ready_to_search_false_for_vague_incomplete() -> None:
    assert not OnboardingState(dish_type="any").ready_to_search()
    assert not OnboardingState().ready_to_search()


# ── onboarding_status_prompt routing ──────────────────────────────────────────

_QUESTIONS = ["Q_dish", "Q_servings", "Q_time", "Q_ingredients", "Q_notes"]


def _prompt(ob: OnboardingState) -> str:
    return onboarding_status_prompt(ob, _QUESTIONS, last_proposals=[], last_recipe=None)


def test_prompt_direct_request_for_concrete_dish() -> None:
    # "Przepis na halloumi dla 2 osób" → dish + servings known, rest empty.
    text = _prompt(OnboardingState(dish_type="halloumi", servings=2))
    assert "DIRECT RECIPE REQUEST" in text
    assert "propose_recipes" in text
    # Must NOT push the guided question march.
    assert "ONBOARDING IN PROGRESS" not in text
    assert "ask ONLY the next missing question" not in text


def test_prompt_guided_for_vague_request() -> None:
    text = _prompt(OnboardingState(dish_type="any"))
    assert "ONBOARDING IN PROGRESS" in text
    assert "DIRECT RECIPE REQUEST" not in text


def test_prompt_guided_when_nothing_collected() -> None:
    text = _prompt(OnboardingState())
    assert "ONBOARDING IN PROGRESS" in text
    assert "DIRECT RECIPE REQUEST" not in text


def test_prompt_empty_when_complete_and_no_proposals() -> None:
    ob = OnboardingState(dish_type="soup", servings=2, max_time_minutes=30,
                         ingredients=[], free_notes="")
    assert _prompt(ob) == ""


# ── Agent build ───────────────────────────────────────────────────────────────

def test_build_chat_agent_returns_agent() -> None:
    assert build_chat_agent(_CONFIG) is not None


def test_agent_registers_expected_tools() -> None:
    agent = build_chat_agent(_CONFIG)
    assert set(agent._function_toolset.tools.keys()) == {
        "update_onboarding",
        "propose_recipes",
        "get_recipe_details",
        "get_recipe_from_url",
        "add_to_calendar",
        "remove_from_calendar",
        "get_shopping_list",
    }


# ── ChatAgentDeps.reset_turn ──────────────────────────────────────────────────

def test_reset_turn_clears_events_and_preserves_durable() -> None:
    from cookbot.agents.chat import FoundRecipe, RecipeOptionsEvent

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
    assert result.complete is False
    assert result.next_missing_field == "max_time_minutes"


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
    assert result.complete is True
    assert result.next_missing_field is None


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


async def test_add_to_calendar_defaults_to_obiad() -> None:
    """Omitting meal_slot must not fail and must land in the default section."""
    deps = _make_deps()
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "add_to_calendar")

    ctx = MagicMock()
    ctx.deps = deps

    result = await fn(ctx, recipe_name="Rosół", ingredients=["kurczak"],
                      target_date="2026-06-01")
    assert _events_of(deps, CalendarAddEvent)[0].entry.meal_slot is MealSlot.OBIAD
    assert result.meal_slot is MealSlot.OBIAD


async def test_add_to_calendar_honours_explicit_meal_slot() -> None:
    deps = _make_deps()
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "add_to_calendar")

    ctx = MagicMock()
    ctx.deps = deps

    result = await fn(ctx, recipe_name="Owsianka", ingredients=["płatki"],
                      target_date="2026-06-01", meal_slot=MealSlot.SNIADANIE)
    assert _events_of(deps, CalendarAddEvent)[0].entry.meal_slot is MealSlot.SNIADANIE
    assert result.meal_slot is MealSlot.SNIADANIE


def test_calendar_entry_without_meal_slot_parses_as_obiad() -> None:
    """Entries persisted before STEP 48 carry no meal_slot — they must still
    parse, defaulting to obiad, or every saved plan would break on load."""
    entry = CalendarEntry.model_validate({
        "id": "1", "date": "2026-06-01",
        "recipe_name": "Legacy", "ingredients": ["x"],
    })
    assert entry.meal_slot is MealSlot.OBIAD


def test_calendar_update_ws_message_round_trips_meal_slot() -> None:
    """The slot reaches the browser through the existing calendar_update message,
    which is why this feature needs no new WsMessageType."""
    from cookbot.protocols.ws_messages import WsOutCalendarUpdate

    entry = CalendarEntry(
        id="1", date="2026-06-01", recipe_name="Owsianka",
        ingredients=["płatki"], meal_slot=MealSlot.SNIADANIE,
    )
    payload = json.loads(WsOutCalendarUpdate(action="add", entry=entry).model_dump_json())
    assert payload["entry"]["meal_slot"] == "sniadanie"


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
        async def run(self, raw_text: str, **_kw):  # noqa: ANN202
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

def _summary(name: str, source: str = "ai_generated", url: str | None = None) -> RecipeSummary:
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


def test_select_proposal_no_match_returns_none() -> None:
    # No silent fallback — guessing the wrong card is worse than asking.
    from cookbot.agents.chat import _select_proposal
    props = [_summary("A"), _summary("B")]
    assert _select_proposal(props, "nonsense") is None


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

    # Accepts the optional kwargs real factories take (e.g. pinned_url).
    return (lambda _config, **_kw: _Stub()), calls


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

    with patch("cookbot.agents.chat.build_web_fetch_agent", lambda _c, **_kw: _FlakyFetch()), \
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


async def test_resolve_recipe_scales_web_recipe_to_requested_servings() -> None:
    # Extraction is verbatim (page serves 2); the user asked for 4, so the scale
    # step runs and its output is applied, with original_servings recorded.
    from cookbot.agents.chat import resolve_recipe
    selected = _summary("Pasta", source="web_search", url="https://x.test/pasta")
    fetch_factory, _ = _stub_agent_factory(_RECIPE)  # _RECIPE.servings == 2
    scaled_ingredients = ["400g pasta", "4 cloves garlic"]
    scale_calls: list[int] = []

    class _ScaleStub:
        async def run(self, *_a, **_k):  # noqa: ANN202
            scale_calls.append(1)
            return MagicMock(output=MagicMock(ingredients=scaled_ingredients))

    with patch("cookbot.agents.chat.build_web_fetch_agent", fetch_factory), \
         patch("cookbot.agents.chat.build_recipe_scale_agent", lambda _c: _ScaleStub()):
        found = await resolve_recipe(
            selected, "1", OnboardingState(servings=4),
            config=_CONFIG, site_filter="", allow_ai_generated=True,
        )

    assert len(scale_calls) == 1
    assert found.recipe.ingredients == scaled_ingredients
    assert found.recipe.servings == 4
    assert found.recipe.original_servings == 2
    assert found.recipe.source_url == "https://x.test/pasta"  # provenance preserved


async def test_resolve_recipe_skips_scaling_when_servings_match() -> None:
    # Page serves 2, user asked for 2 → the scale agent must NOT be consulted.
    from cookbot.agents.chat import resolve_recipe
    selected = _summary("Pasta", source="web_search", url="https://x.test/pasta")
    fetch_factory, _ = _stub_agent_factory(_RECIPE)  # servings == 2

    class _ScaleStub:
        async def run(self, *_a, **_k):  # noqa: ANN202
            raise AssertionError("scale agent should not run when servings match")

    with patch("cookbot.agents.chat.build_web_fetch_agent", fetch_factory), \
         patch("cookbot.agents.chat.build_recipe_scale_agent", lambda _c: _ScaleStub()):
        found = await resolve_recipe(
            selected, "1", OnboardingState(servings=2),
            config=_CONFIG, site_filter="", allow_ai_generated=True,
        )

    assert found.recipe.ingredients == _RECIPE.ingredients  # unchanged
    assert found.recipe.servings == 2
    assert found.recipe.original_servings == 2


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


# ── get_recipe_from_url tool ──────────────────────────────────────────────────

async def test_get_recipe_from_url_extracts_and_shows_card() -> None:
    from cookbot.agents.chat import FinalRecipeEvent, FoundRecipe
    deps = _make_deps()
    # Explicit source_url=None so the backfill assertion is deterministic, and a
    # copy so the tool's in-place backfill doesn't mutate the shared _RECIPE.
    fetch_factory, fetch_calls = _stub_agent_factory(_RECIPE.model_copy(update={"source_url": None}))

    with patch("cookbot.agents.chat.build_web_fetch_agent", fetch_factory):
        agent = build_chat_agent(_CONFIG)
        fn = _get_tool(agent, "get_recipe_from_url")
        ctx = MagicMock()
        ctx.deps = deps
        ctx.usage = None
        found = await fn(ctx, url="https://kwestiasmaku.com/przepis/x")

    assert isinstance(found, FoundRecipe)
    assert found.source == "web_search"
    assert len(fetch_calls) == 1
    # last_recipe set so add_to_calendar can attach it; card emitted.
    assert deps.last_recipe is found
    events = _events_of(deps, FinalRecipeEvent)
    assert len(events) == 1
    # Provenance: source_url backfilled from the pasted URL (the stub recipe had none).
    assert deps.last_recipe.recipe.source_url == "https://kwestiasmaku.com/przepis/x"


async def test_get_recipe_from_url_not_found_emits_no_card() -> None:
    from cookbot.agents.chat import FinalRecipeEvent
    deps = _make_deps()
    fetch_factory, _ = _stub_agent_factory(None)  # page has no readable recipe

    with patch("cookbot.agents.chat.build_web_fetch_agent", fetch_factory):
        agent = build_chat_agent(_CONFIG)
        fn = _get_tool(agent, "get_recipe_from_url")
        ctx = MagicMock()
        ctx.deps = deps
        ctx.usage = None
        found = await fn(ctx, url="https://example.com/not-a-recipe")

    assert found.source == "not_found"
    assert _events_of(deps, FinalRecipeEvent) == []


async def test_get_recipe_from_url_then_add_to_calendar_carries_recipe() -> None:
    """The end-to-end point: paste URL → extract → add to calendar with the real
    recipe attached (so the shopping list gets its ingredients)."""
    from cookbot.agents.chat import CalendarAddEvent
    deps = _make_deps()
    fetch_factory, _ = _stub_agent_factory(_RECIPE.model_copy())

    with patch("cookbot.agents.chat.build_web_fetch_agent", fetch_factory):
        agent = build_chat_agent(_CONFIG)
        ctx = MagicMock()
        ctx.deps = deps
        ctx.usage = None
        await _get_tool(agent, "get_recipe_from_url")(ctx, url="https://x.test/przepis")
        add = await _get_tool(agent, "add_to_calendar")(
            ctx, recipe_name="Test Pasta", ingredients=_RECIPE.ingredients,
            target_date="2026-08-10",
        )

    assert add.date == "2026-08-10"
    add_events = _events_of(deps, CalendarAddEvent)
    assert len(add_events) == 1
    entry = add_events[0].entry
    # The full recipe (with ingredients) is attached to the calendar entry.
    assert entry.recipe is not None
    assert entry.recipe["ingredients"] == _RECIPE.ingredients


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


# ── Ambiguous selection → ModelRetry, never a guessed card ───────────────────

async def test_get_recipe_details_ambiguous_choice_raises_model_retry() -> None:
    from pydantic_ai import ModelRetry

    deps = _make_deps(last_proposals=[_summary("Pasta"), _summary("Soup")])
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "get_recipe_details")
    ctx = MagicMock()
    ctx.deps = deps

    with pytest.raises(ModelRetry):
        await fn(ctx, choice="które jest najzdrowsze?")

    # Proposals stay live so the user can still pick after the clarification.
    assert len(deps.last_proposals) == 2
    assert _events_of(deps, FinalRecipeEvent) == []


# ── Tool error containment (a sub-agent failure must not crash the turn) ─────

async def test_propose_recipes_failure_returns_structured_error() -> None:
    deps = _make_deps()
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "propose_recipes")
    ctx = MagicMock()
    ctx.deps = deps

    class _BoomAgent:
        async def run(self, *_a, **_k):  # noqa: ANN202
            raise RuntimeError("openai 429")

    with patch("cookbot.agents.chat.build_recipe_options_agent", lambda _c: _BoomAgent()):
        result = await fn(ctx, dish_type="pasta", ingredients=[])

    assert result.count == 0
    assert "error" in result.message
    assert deps.last_proposals == []                     # nothing recorded
    assert _events_of(deps, RecipeOptionsEvent) == []    # nothing emitted


async def test_get_recipe_details_failure_returns_error_source() -> None:
    proposals = [_summary("Pasta", source="web_search", url="https://x.test/p")]
    deps = _make_deps(last_proposals=list(proposals))
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "get_recipe_details")
    ctx = MagicMock()
    ctx.deps = deps

    class _BoomAgent:
        async def run(self, *_a, **_k):  # noqa: ANN202
            raise RuntimeError("network down")

    with patch("cookbot.agents.chat.build_web_fetch_agent", lambda _c: _BoomAgent()):
        found = await fn(ctx, choice="1")

    assert found.source == "error"
    assert _events_of(deps, FinalRecipeEvent) == []      # no card for the error
    # Proposals survive the failure so the user can simply retry the pick.
    assert len(deps.last_proposals) == 1


async def test_get_shopping_list_failure_returns_error_field() -> None:
    calendar = CalendarState(entries=[
        CalendarEntry(id="1", date="2026-06-01", recipe_name="Pasta",
                      ingredients=["pasta"]),
    ])
    deps = _make_deps(calendar=calendar)
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "get_shopping_list")
    ctx = MagicMock()
    ctx.deps = deps

    class _BoomAgent:
        async def run(self, *_a, **_k):  # noqa: ANN202
            raise RuntimeError("openai down")

    with patch("cookbot.agents.chat.build_shopping_list_agent", lambda _c: _BoomAgent()):
        result = await fn(ctx, date_from="2026-06-01", date_to="2026-06-05")

    assert result.item_count == 0
    assert result.error is not None
    assert _events_of(deps, ShoppingListEvent) == []


# ── Sub-agent caching (one build per connection, not per call) ───────────────

async def test_sub_agent_built_once_per_connection() -> None:
    calendar = CalendarState(entries=[
        CalendarEntry(id="1", date="2026-06-01", recipe_name="Pasta",
                      ingredients=["pasta"]),
    ])
    deps = _make_deps(calendar=calendar)
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "get_shopping_list")
    ctx = MagicMock()
    ctx.deps = deps

    fake_list = ShoppingList(items=[], sections=[])
    builds: list[int] = []

    class _StubAgent:
        async def run(self, *_a, **_k):  # noqa: ANN202
            return MagicMock(output=fake_list)

    def _factory(_config):  # noqa: ANN202
        builds.append(1)
        return _StubAgent()

    with patch("cookbot.agents.chat.build_shopping_list_agent", _factory):
        await fn(ctx, date_from="2026-06-01", date_to="2026-06-05")
        await fn(ctx, date_from="2026-06-01", date_to="2026-06-05")

    assert len(builds) == 1


# ── ChatState persistence (dump → restore roundtrip) ─────────────────────────

def _sample_history() -> list:
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )
    return [
        ModelRequest(parts=[UserPromptPart(content="zrób mi pastę")]),
        ModelResponse(parts=[TextPart(content="Jasne! Na ile osób?")]),
    ]


def test_chat_state_roundtrip_restores_history_and_deps() -> None:
    from cookbot.agents.chat import FoundRecipe, dump_chat_state, restore_chat_state

    deps = _make_deps(
        onboarding=OnboardingState(dish_type="pasta", servings=2),
        last_recipe=FoundRecipe(recipe=_RECIPE, source="ai_generated"),
        last_proposals=[_summary("Pasta", source="web_search", url="https://x.test/p")],
    )
    history = _sample_history()

    raw = dump_chat_state(deps, history)

    fresh = _make_deps()
    restored_history = restore_chat_state(raw, fresh)

    assert fresh.onboarding.dish_type == "pasta"
    assert fresh.onboarding.servings == 2
    assert fresh.last_recipe is not None
    assert fresh.last_recipe.recipe.name == "Test Pasta"
    assert [p.name for p in fresh.last_proposals] == ["Pasta"]
    assert len(restored_history) == len(history)


def test_chat_state_dump_is_firestore_safe() -> None:
    # The snapshot must be a plain JSON-able dict with the message history as a
    # single string (Firestore rejects directly nested arrays).
    import json

    from cookbot.agents.chat import dump_chat_state

    deps = _make_deps()
    raw = dump_chat_state(deps, _sample_history())

    assert isinstance(raw, dict)
    assert isinstance(raw["messages_json"], str)
    json.dumps(raw)  # fully JSON-serializable


def test_restore_chat_state_empty_snapshot_yields_fresh_state() -> None:
    from cookbot.agents.chat import dump_chat_state, restore_chat_state

    deps = _make_deps()
    raw = dump_chat_state(deps, [])

    fresh = _make_deps()
    history = restore_chat_state(raw, fresh)

    assert history == []
    assert not fresh.onboarding.complete
    assert fresh.last_recipe is None
    assert fresh.last_proposals == []


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


# ── _url_from_user_message ────────────────────────────────────────────────────
# The model retypes a pasted URL into the tool argument and can corrupt long
# slugs (observed live: ".../chlebkiem-naan/" → ".../chlebkiem-naaan/", a 404
# that surfaced to the user as "this page has no recipe"). The user's own message
# is the source of truth for the link.

def test_url_recovered_from_user_message_when_model_mistypes() -> None:
    from cookbot.agents.chat import _url_from_user_message

    real = "https://chilitonka.com/2013/09/07/prawdopodobnie-najlepsze-curry-naan/"
    typo = "https://chilitonka.com/2013/09/07/prawdopodobnie-najlepsze-curry-naaan/"
    assert _url_from_user_message(typo, f"Dodaj przepis dla 4 osób z {real}") == real


def test_url_falls_back_to_model_arg_when_message_has_no_url() -> None:
    from cookbot.agents.chat import _url_from_user_message

    model_url = "https://example.com/przepis"
    # Link came from an earlier turn — nothing to recover from this message.
    assert _url_from_user_message(model_url, "dodaj to do kalendarza") == model_url


def test_url_picks_closest_match_when_message_has_several() -> None:
    from cookbot.agents.chat import _url_from_user_message

    a = "https://aniagotuje.pl/przepis/makaron"
    b = "https://chilitonka.com/2013/09/07/curry-naan/"
    msg = f"Wolę ten {a} albo ten {b}"
    # The model was clearly aiming at the chilitonka one (mistyped slug).
    assert _url_from_user_message("https://chilitonka.com/2013/09/07/curry-naaan/", msg) == b


def test_url_trailing_punctuation_not_swallowed() -> None:
    from cookbot.agents.chat import _url_from_user_message

    url = "https://chilitonka.com/curry-naan/"
    assert _url_from_user_message(url, f"Zrób to z {url} (dla 4 osób)") == url


# ── propose_recipes persists the direct-request context (STEP 46) ─────────────
# A direct request ("przepis na halloumi dla 4 osób") reaches propose_recipes
# without update_onboarding running first. The arguments must be written back to
# deps.onboarding, because resolve_recipe / get_recipe_from_url read `servings`
# ONLY from there when scaling the chosen recipe.

async def _run_propose(deps, **kwargs):
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "propose_recipes")
    ctx = MagicMock()
    ctx.deps = deps

    class _Opts:
        async def run(self, *_a, **_k):  # noqa: ANN202
            return MagicMock(output=MagicMock(proposals=[]))

    with patch("cookbot.agents.chat.build_recipe_options_agent", lambda _c: _Opts()):
        return await fn(ctx, **kwargs)


async def test_propose_recipes_records_direct_request_context() -> None:
    deps = _make_deps()
    assert deps.onboarding.servings is None

    await _run_propose(deps, dish_type="halloumi", ingredients=[], servings=4,
                       max_time_minutes=30)

    # Without this, the recipe would later scale to the `or 2` default.
    assert deps.onboarding.servings == 4
    assert deps.onboarding.dish_type == "halloumi"
    assert deps.onboarding.max_time_minutes == 30


async def test_propose_recipes_does_not_overwrite_existing_answers() -> None:
    """Guided onboarding already collected these — a later call must not clobber
    them with its own defaults (servings defaults to 2 in the signature)."""
    deps = _make_deps()
    deps.onboarding.servings = 6
    deps.onboarding.dish_type = "zupa"

    await _run_propose(deps, dish_type="halloumi", ingredients=[])

    assert deps.onboarding.servings == 6, "user's stated servings was overwritten"
    assert deps.onboarding.dish_type == "zupa", "user's stated dish was overwritten"


# ── Fast path routing (STEP 47) ───────────────────────────────────────────────
# A plain "przepis na X" must reach the zero-LLM DDG path and must NOT build the
# RecipeOptionsAgent at all. The negative assertion is the important one: the
# whole point of the fast path is that no model call happens inside the tool.

async def _run_propose_routed(deps, fast_result, **kwargs):
    """Run propose_recipes with both paths stubbed, reporting which one ran."""
    agent = build_chat_agent(_CONFIG)
    fn = _get_tool(agent, "propose_recipes")
    ctx = MagicMock()
    ctx.deps = deps
    built: list[str] = []

    class _Opts:
        async def run(self, *_a, **_k):  # noqa: ANN202
            return MagicMock(output=MagicMock(proposals=[
                _proposal(f"llm-{i}") for i in range(4)
            ]))

    def _build_opts(_c):
        built.append("recipe_options")
        return _Opts()

    async def _fake_fast(_query, limit):  # noqa: ANN202
        return fast_result[:limit]

    with patch("cookbot.agents.chat.build_recipe_options_agent", _build_opts), \
         patch("cookbot.agents.chat.build_fast_proposals", _fake_fast), \
         patch("cookbot.agents.chat.populate_proposal_images", _noop_images):
        result = await fn(ctx, **kwargs)
    return result, built


async def _noop_images(_proposals):  # noqa: ANN202
    return None


def _proposal(name: str) -> RecipeSummary:
    return RecipeSummary(
        name=name, description="d", difficulty="", total_time_minutes=0,
        key_ingredients=[], source="web_search", source_url=f"https://s.test/{name}",
    )


async def test_plain_request_uses_fast_path_and_never_builds_llm_agent() -> None:
    deps = _make_deps(current_user_message="znajdź przepis na jagodzianki")
    fast = [_proposal(f"fast-{i}") for i in range(6)]

    result, built = await _run_propose_routed(deps, fast, dish_type="jagodzianki", ingredients=[])

    assert built == [], "RecipeOptionsAgent was built — the LLM path ran"
    assert result.count == 6
    assert [p.name for p in deps.last_proposals] == [f"fast-{i}" for i in range(6)]


async def test_constrained_request_uses_llm_path() -> None:
    deps = _make_deps(current_user_message="przepis na jagodzianki bez cukru")
    fast = [_proposal(f"fast-{i}") for i in range(6)]

    result, built = await _run_propose_routed(
        deps, fast, dish_type="jagodzianki", ingredients=[], free_notes="bez cukru",
    )

    assert built == ["recipe_options"], "constrained request skipped the reasoning agent"
    assert result.count == 4


async def test_fast_path_below_minimum_falls_back_to_llm_path() -> None:
    """Two good pages is a thin result — fall through rather than show it."""
    deps = _make_deps(current_user_message="znajdź przepis na jagodzianki")
    fast = [_proposal("fast-0"), _proposal("fast-1")]

    result, built = await _run_propose_routed(deps, fast, dish_type="jagodzianki", ingredients=[])

    assert built == ["recipe_options"]
    assert result.count == 4
    assert all(p.name.startswith("llm-") for p in deps.last_proposals)


async def test_fast_path_still_records_servings_for_scaling() -> None:
    """STEP 46's guarantee must survive the prompt trim: a direct "dla 4 osób"
    request ends with servings recorded, because that is what the chosen recipe
    is later scaled to."""
    deps = _make_deps(current_user_message="przepis na jagodzianki dla 4 osób")
    fast = [_proposal(f"fast-{i}") for i in range(6)]

    await _run_propose_routed(deps, fast, dish_type="jagodzianki", ingredients=[], servings=4)

    assert deps.onboarding.servings == 4
    assert deps.onboarding.dish_type == "jagodzianki"


async def test_six_proposals_are_selectable_by_number_and_name() -> None:
    """_select_proposal must handle a 6-card list — "6" is only valid on the fast path."""
    from cookbot.agents.chat import _select_proposal

    proposals = [_proposal(f"fast-{i}") for i in range(6)]
    assert _select_proposal(proposals, "6") is proposals[5]
    assert _select_proposal(proposals, "fast-4") is proposals[4]
    assert _select_proposal(proposals, "9") is None
