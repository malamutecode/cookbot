import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from cookbot.exceptions import HITLTimeoutError
from cookbot.hitl.gate import HITLGate
from cookbot.hitl.models import HITLCheckpoint, HITLResponse
from cookbot.models.recipe import Recipe

_RECIPE = Recipe(
    name="Test Recipe",
    description="A test.",
    ingredients=["ingredient 1"],
    steps=["Step 1."],
    prep_time_minutes=5,
    cook_time_minutes=10,
    difficulty="Easy",
    servings=2,
    tips=[],
)


def _make_firestore() -> AsyncMock:
    mock = AsyncMock()
    mock.save_hitl_checkpoint = AsyncMock(return_value=None)
    mock.clear_hitl_checkpoint = AsyncMock(return_value=None)
    mock.get_hitl_checkpoint = AsyncMock(return_value=None)
    return mock


def _make_gate() -> HITLGate:
    return HITLGate(session_id="test-session", firestore=_make_firestore())


# ── Concurrency: suspend blocks until submit_response is called ───────────────

async def test_suspend_blocks_until_response() -> None:
    gate = _make_gate()
    approved_response = HITLResponse(approved=True)

    async def pipeline() -> HITLResponse:
        return await gate.suspend(_RECIPE, round_number=1)

    async def user_interaction() -> None:
        checkpoint = await gate.get_checkpoint()
        assert checkpoint.round_number == 1
        await gate.submit_response(approved_response)

    pipeline_task = asyncio.create_task(pipeline())
    await user_interaction()
    result = await pipeline_task
    assert result.approved is True


async def test_response_value_flows_correctly() -> None:
    gate = _make_gate()
    modification_response = HITLResponse(approved=False, modification="make it vegan")

    async def pipeline() -> HITLResponse:
        return await gate.suspend(_RECIPE, round_number=1)

    pipeline_task = asyncio.create_task(pipeline())
    await gate.get_checkpoint()
    await gate.submit_response(modification_response)
    result = await pipeline_task

    assert result.approved is False
    assert result.modification == "make it vegan"


async def test_rejected_response_propagates() -> None:
    gate = _make_gate()
    rejected = HITLResponse(approved=False, modification=None)

    async def pipeline() -> HITLResponse:
        return await gate.suspend(_RECIPE, round_number=1)

    pipeline_task = asyncio.create_task(pipeline())
    await gate.get_checkpoint()
    await gate.submit_response(rejected)
    result = await pipeline_task

    assert result.approved is False
    assert result.modification is None


async def test_timeout_raises_hitl_timeout_error() -> None:
    gate = _make_gate()

    with pytest.raises(HITLTimeoutError):
        await gate.suspend(_RECIPE, round_number=1, timeout=0.05)


async def test_checkpoint_saved_to_firestore() -> None:
    firestore = _make_firestore()
    gate = HITLGate(session_id="test-session", firestore=firestore)

    async def pipeline() -> HITLResponse:
        return await gate.suspend(_RECIPE, round_number=2)

    pipeline_task = asyncio.create_task(pipeline())
    await gate.get_checkpoint()
    await gate.submit_response(HITLResponse(approved=True))
    await pipeline_task

    firestore.save_hitl_checkpoint.assert_called_once()
    saved: HITLCheckpoint = firestore.save_hitl_checkpoint.call_args[0][0]
    assert saved.session_id == "test-session"
    assert saved.round_number == 2


async def test_checkpoint_cleared_after_response() -> None:
    firestore = _make_firestore()
    gate = HITLGate(session_id="test-session", firestore=firestore)

    async def pipeline() -> HITLResponse:
        return await gate.suspend(_RECIPE, round_number=1)

    pipeline_task = asyncio.create_task(pipeline())
    await gate.get_checkpoint()
    await gate.submit_response(HITLResponse(approved=True))
    await pipeline_task

    firestore.clear_hitl_checkpoint.assert_called_once_with("test-session")


async def test_multiple_rounds_sequential() -> None:
    gate = _make_gate()
    results: list[HITLResponse] = []

    async def pipeline() -> None:
        results.append(await gate.suspend(_RECIPE, round_number=1))
        results.append(await gate.suspend(_RECIPE, round_number=2))

    pipeline_task = asyncio.create_task(pipeline())

    await gate.get_checkpoint()
    await gate.submit_response(HITLResponse(approved=False, modification="less salt"))

    await gate.get_checkpoint()
    await gate.submit_response(HITLResponse(approved=True))

    await pipeline_task

    assert results[0].modification == "less salt"
    assert results[1].approved is True
