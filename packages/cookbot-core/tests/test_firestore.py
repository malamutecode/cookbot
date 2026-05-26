import os
from datetime import UTC, datetime, timedelta

import pytest

from cookbot.hitl.models import HITLCheckpoint, HITLResponse
from cookbot.models.recipe import Recipe
from cookbot.models.session import Message, Session, SessionStatus
from cookbot.services.firestore import FirestoreService

EMULATOR_HOST = os.getenv("FIRESTORE_EMULATOR_HOST", "")
emulator_available = pytest.mark.skipif(
    not EMULATOR_HOST,
    reason="FIRESTORE_EMULATOR_HOST not set — start emulator first",
)

PROJECT = "test-project"
DATABASE = "(default)"
TENANT = "test-tenant"


def _make_service() -> FirestoreService:
    return FirestoreService(project_id=PROJECT, database_id=DATABASE, tenant_id=TENANT)


def _sample_recipe() -> Recipe:
    return Recipe(
        name="Test Pasta",
        description="Quick pasta dish.",
        ingredients=["pasta", "tomato sauce"],
        steps=["Boil pasta.", "Add sauce."],
        prep_time_minutes=5,
        cook_time_minutes=10,
        difficulty="Easy",
        servings=2,
        tips=[],
    )


def _sample_session(session_id: str) -> Session:
    now = datetime.now(UTC)
    return Session(
        session_id=session_id,
        tenant_id=TENANT,
        status=SessionStatus.ACTIVE,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )


@emulator_available
async def test_save_and_get_message() -> None:
    svc = _make_service()
    session_id = "sess-msg-test"
    msg = Message(role="user", content="Hello emulator!")

    await svc.save_message(session_id, msg)
    messages = await svc.get_messages(session_id)

    assert len(messages) == 1
    assert messages[0].content == "Hello emulator!"
    assert messages[0].role == "user"


@emulator_available
async def test_multiple_messages_append() -> None:
    svc = _make_service()
    session_id = "sess-multi-msg"
    await svc.save_message(session_id, Message(role="user", content="First"))
    await svc.save_message(session_id, Message(role="assistant", content="Second"))

    messages = await svc.get_messages(session_id)
    assert len(messages) == 2
    assert messages[1].content == "Second"


@emulator_available
async def test_get_messages_empty_session() -> None:
    svc = _make_service()
    messages = await svc.get_messages("nonexistent-session-xyz")
    assert messages == []


@emulator_available
async def test_save_and_get_session() -> None:
    svc = _make_service()
    session_id = "sess-save-test"
    session = _sample_session(session_id)

    await svc.save_session(session)
    loaded = await svc.get_session(session_id)

    assert loaded is not None
    assert loaded.session_id == session_id
    assert loaded.tenant_id == TENANT
    assert loaded.status == SessionStatus.ACTIVE


@emulator_available
async def test_get_session_returns_none_for_unknown() -> None:
    svc = _make_service()
    result = await svc.get_session("does-not-exist-abc123")
    assert result is None


@emulator_available
async def test_save_and_get_hitl_checkpoint() -> None:
    svc = _make_service()
    session_id = "sess-hitl-test"
    checkpoint = HITLCheckpoint(
        checkpoint_id="cp-1",
        session_id=session_id,
        recipe=_sample_recipe(),
        round_number=1,
        created_at=datetime.now(UTC),
    )

    await svc.save_hitl_checkpoint(checkpoint)
    loaded = await svc.get_hitl_checkpoint(session_id)

    assert loaded is not None
    assert loaded.checkpoint_id == "cp-1"
    assert loaded.recipe.name == "Test Pasta"
    assert loaded.round_number == 1


@emulator_available
async def test_clear_hitl_checkpoint() -> None:
    svc = _make_service()
    session_id = "sess-hitl-clear"
    checkpoint = HITLCheckpoint(
        checkpoint_id="cp-clear",
        session_id=session_id,
        recipe=_sample_recipe(),
        round_number=1,
        created_at=datetime.now(UTC),
    )

    await svc.save_hitl_checkpoint(checkpoint)
    assert await svc.get_hitl_checkpoint(session_id) is not None

    await svc.clear_hitl_checkpoint(session_id)
    assert await svc.get_hitl_checkpoint(session_id) is None


@emulator_available
async def test_get_hitl_checkpoint_returns_none_when_absent() -> None:
    svc = _make_service()
    result = await svc.get_hitl_checkpoint("fresh-session-no-hitl")
    assert result is None
