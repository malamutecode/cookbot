import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai.models.test import TestModel

from cookbot.hitl.gate import HITLGate
from cookbot.hitl.models import HITLResponse
from cookbot.models.recipe import (
    ParsedIngredients,
    Recipe,
    RecipeSource,
    UserIntent,
)
from cookbot.models.tenant import TenantConfig
from cookbot.orchestrator.session import SessionOrchestrator

_CONFIG = TenantConfig(
    tenant_id="test",
    persona="You are a helpful chef",
    language="en",
    recipe_source_url="",
    allowed_origins=[],
)

_INTENT = UserIntent(
    dish_type="pasta",
    servings=2,
    max_time_minutes=30,
    available_ingredients=["pasta", "tomato"],
    free_notes="",
)

_INGREDIENTS = ParsedIngredients(
    items=["pasta", "tomato"],
    must_use=["pasta"],
    dietary_hints=[],
    missing_staples=["salt"],
)

_RECIPE_DICT = {
    "name": "Tomato Pasta",
    "description": "Simple pasta dish.",
    "ingredients": ["200g pasta", "2 tomatoes", "salt"],
    "steps": ["Boil pasta.", "Make sauce.", "Combine."],
    "prep_time_minutes": 5,
    "cook_time_minutes": 20,
    "difficulty": "Easy",
    "servings": 2,
    "tips": [],
}

_RECIPE = Recipe(**_RECIPE_DICT)


def _make_firestore() -> AsyncMock:
    mock = AsyncMock()
    mock.save_message = AsyncMock(return_value=None)
    mock.save_hitl_checkpoint = AsyncMock(return_value=None)
    mock.clear_hitl_checkpoint = AsyncMock(return_value=None)
    mock.get_hitl_checkpoint = AsyncMock(return_value=None)
    return mock


def _make_gate(firestore: AsyncMock) -> HITLGate:
    return HITLGate(session_id="test-session", firestore=firestore)


def _make_callbacks():
    tokens: list[str] = []
    agent_updates: list[tuple[str, str]] = []
    final_recipes: list[tuple[Recipe, RecipeSource]] = []
    errors: list[str] = []

    async def on_token(content: str) -> None:
        tokens.append(content)

    async def on_agent_update(agent: str, status: str) -> None:
        agent_updates.append((agent, status))

    async def on_final_recipe(recipe: Recipe, source: RecipeSource) -> None:
        final_recipes.append((recipe, source))

    async def on_error(message: str) -> None:
        errors.append(message)

    return tokens, agent_updates, final_recipes, errors, {
        "on_token": on_token,
        "on_agent_update": on_agent_update,
        "on_final_recipe": on_final_recipe,
        "on_error": on_error,
    }


def _agent_result(output) -> MagicMock:
    """Fake PydanticAI RunResult with .output attribute."""
    result = MagicMock()
    result.output = output
    return result


def _mock_agent(output) -> AsyncMock:
    """AsyncMock whose .run() returns a result with given output."""
    agent = AsyncMock()
    agent.run = AsyncMock(return_value=_agent_result(output))
    return agent


async def _drive_gate_once(gate: HITLGate, response: HITLResponse) -> None:
    """Simulate the WS handler: wait for checkpoint then submit response."""
    await gate.get_checkpoint()
    await gate.submit_response(response)


_WS_MODULE = "cookbot.orchestrator.session.build_web_search_agent"
_RG_MODULE = "cookbot.orchestrator.session.build_recipe_gen_agent"
_REF_MODULE = "cookbot.orchestrator.session.build_refinement_agent"


# ── Happy path: web search finds recipe, user approves ───────────────────────

async def test_web_search_hit_approved_first_round() -> None:
    firestore = _make_firestore()
    gate = _make_gate(firestore)
    orchestrator = SessionOrchestrator(_CONFIG, firestore)
    tokens, agent_updates, final_recipes, errors, cbs = _make_callbacks()

    with (
        patch(_WS_MODULE, return_value=_mock_agent(_RECIPE)),
        patch(_RG_MODULE, return_value=_mock_agent(_RECIPE)),
        patch(_REF_MODULE, return_value=_mock_agent(_RECIPE)),
    ):
        gate_task = asyncio.create_task(
            _drive_gate_once(gate, HITLResponse(approved=True))
        )
        await orchestrator.run(
            session_id="test-session",
            intent=_INTENT,
            ingredients=_INGREDIENTS,
            gate=gate,
            **cbs,
        )
        await gate_task

    assert len(final_recipes) == 1
    assert final_recipes[0][1] == RecipeSource.WEB_SEARCH
    assert ("web_search", "searching") in agent_updates
    assert len(errors) == 0


# ── Web search returns None → fallback to recipe gen ─────────────────────────

async def test_recipe_gen_fallback_when_web_search_returns_none() -> None:
    firestore = _make_firestore()
    gate = _make_gate(firestore)
    orchestrator = SessionOrchestrator(_CONFIG, firestore)
    _, agent_updates, final_recipes, errors, cbs = _make_callbacks()

    with (
        patch(_WS_MODULE, return_value=_mock_agent(None)),
        patch(_RG_MODULE, return_value=_mock_agent(_RECIPE)),
        patch(_REF_MODULE, return_value=_mock_agent(_RECIPE)),
    ):
        gate_task = asyncio.create_task(
            _drive_gate_once(gate, HITLResponse(approved=True))
        )
        await orchestrator.run(
            session_id="test-session",
            intent=_INTENT,
            ingredients=_INGREDIENTS,
            gate=gate,
            **cbs,
        )
        await gate_task

    assert len(final_recipes) == 1
    assert final_recipes[0][1] == RecipeSource.AI_GENERATED
    assert ("recipe_gen", "generating") in agent_updates
    assert len(errors) == 0


# ── HITL: user requests modification, then approves ──────────────────────────

async def test_hitl_modification_then_approval() -> None:
    firestore = _make_firestore()
    gate = _make_gate(firestore)
    orchestrator = SessionOrchestrator(_CONFIG, firestore)
    _, agent_updates, final_recipes, errors, cbs = _make_callbacks()

    async def drive_two_rounds() -> None:
        await _drive_gate_once(gate, HITLResponse(approved=False, modification="make it vegan"))
        await _drive_gate_once(gate, HITLResponse(approved=True))

    with (
        patch(_WS_MODULE, return_value=_mock_agent(_RECIPE)),
        patch(_RG_MODULE, return_value=_mock_agent(_RECIPE)),
        patch(_REF_MODULE, return_value=_mock_agent(_RECIPE)),
    ):
        gate_task = asyncio.create_task(drive_two_rounds())
        await orchestrator.run(
            session_id="test-session",
            intent=_INTENT,
            ingredients=_INGREDIENTS,
            gate=gate,
            **cbs,
        )
        await gate_task

    assert len(final_recipes) == 1
    assert final_recipes[0][1] == RecipeSource.AI_GENERATED
    assert ("refinement", "refining round 1") in agent_updates
    assert len(errors) == 0


# ── HITL: user rejects recipe (modification=None) ────────────────────────────

async def test_hitl_rejection_calls_on_error() -> None:
    firestore = _make_firestore()
    gate = _make_gate(firestore)
    orchestrator = SessionOrchestrator(_CONFIG, firestore)
    _, _, final_recipes, errors, cbs = _make_callbacks()

    with (
        patch(_WS_MODULE, return_value=_mock_agent(_RECIPE)),
        patch(_RG_MODULE, return_value=_mock_agent(_RECIPE)),
        patch(_REF_MODULE, return_value=_mock_agent(_RECIPE)),
    ):
        gate_task = asyncio.create_task(
            _drive_gate_once(gate, HITLResponse(approved=False, modification=None))
        )
        await orchestrator.run(
            session_id="test-session",
            intent=_INTENT,
            ingredients=_INGREDIENTS,
            gate=gate,
            **cbs,
        )
        await gate_task

    assert len(final_recipes) == 0
    assert len(errors) == 1
    assert "rejected" in errors[0].lower()


# ── Messages saved to Firestore ───────────────────────────────────────────────

async def test_messages_saved_to_firestore() -> None:
    firestore = _make_firestore()
    gate = _make_gate(firestore)
    orchestrator = SessionOrchestrator(_CONFIG, firestore)
    _, _, _, _, cbs = _make_callbacks()

    with (
        patch(_WS_MODULE, return_value=_mock_agent(_RECIPE)),
        patch(_RG_MODULE, return_value=_mock_agent(_RECIPE)),
        patch(_REF_MODULE, return_value=_mock_agent(_RECIPE)),
    ):
        gate_task = asyncio.create_task(
            _drive_gate_once(gate, HITLResponse(approved=True))
        )
        await orchestrator.run(
            session_id="test-session",
            intent=_INTENT,
            ingredients=_INGREDIENTS,
            gate=gate,
            **cbs,
        )
        await gate_task

    # "Recipe generated: ..." + "Final recipe delivered: ..."
    assert firestore.save_message.call_count >= 2


# ── Exception in pipeline triggers on_error ──────────────────────────────────

async def test_exception_calls_on_error() -> None:
    firestore = _make_firestore()
    gate = _make_gate(firestore)
    orchestrator = SessionOrchestrator(_CONFIG, firestore)
    _, _, final_recipes, errors, cbs = _make_callbacks()

    broken_agent = AsyncMock()
    broken_agent.run = AsyncMock(side_effect=RuntimeError("network failure"))

    with (
        patch(_WS_MODULE, return_value=broken_agent),
        patch(_RG_MODULE, return_value=_mock_agent(_RECIPE)),
        patch(_REF_MODULE, return_value=_mock_agent(_RECIPE)),
    ):
        await orchestrator.run(
            session_id="test-session",
            intent=_INTENT,
            ingredients=_INGREDIENTS,
            gate=gate,
            **cbs,
        )

    assert len(errors) == 1
    assert "network failure" in errors[0]
    assert len(final_recipes) == 0
