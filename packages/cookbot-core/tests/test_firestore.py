import os
from datetime import UTC, datetime, timedelta

import pytest

from cookbot.hitl.models import HITLCheckpoint
from cookbot.models.recipe import Recipe
from cookbot.models.session import Message, Session, SessionStatus
from cookbot.services.firestore import FirestoreService

# These hit the real Firestore emulator → integration, excluded from the fast
# unit run (`-m "not integration"`). The skipif still applies when running
# `-m integration` without the emulator up.
pytestmark = pytest.mark.integration

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


# ── User records + token quotas (STEP 42) ────────────────────────────────────

@emulator_available
async def test_get_user_record_creates_default() -> None:
    from cookbot.models.user import TokenQuota

    svc = _make_service()
    rec = await svc.get_user_record(
        "quota-user-default",
        default_quota=TokenQuota(daily_limit=500, monthly_limit=9000),
    )
    assert rec.role == "user"
    assert rec.quota.daily_limit == 500
    assert rec.disabled is False


@emulator_available
async def test_get_user_record_seeds_admin_from_admin_uids() -> None:
    svc = _make_service()
    rec = await svc.get_user_record(
        "quota-admin-seed",
        admin_uids=frozenset({"quota-admin-seed"}),
    )
    assert rec.is_admin


@emulator_available
async def test_add_usage_increments_and_lazy_resets() -> None:
    import uuid

    svc = _make_service()
    # Unique uid so reruns against the persistent emulator stay isolated.
    uid = f"quota-usage-{uuid.uuid4().hex[:8]}"

    # Two adds to the same day/month keys accumulate.
    await svc.add_usage(uid, ["2026-07-07", "2026-07"], 100)
    await svc.add_usage(uid, ["2026-07-07", "2026-07"], 250)
    day = await svc.get_usage_counter(uid, "2026-07-07")
    month = await svc.get_usage_counter(uid, "2026-07")
    assert day.tokens_used == 350
    assert month.tokens_used == 350

    # A new day key starts fresh (lazy reset by construction — different doc).
    await svc.add_usage(uid, ["2026-07-08", "2026-07"], 40)
    new_day = await svc.get_usage_counter(uid, "2026-07-08")
    same_month = await svc.get_usage_counter(uid, "2026-07")
    assert new_day.tokens_used == 40
    assert same_month.tokens_used == 390  # month kept accumulating


# ── Calendar (STEP 52) ───────────────────────────────────────────────────────

def _cal_entry(entry_id: str, date: str = "2026-07-26"):
    from cookbot.models.calendar import CalendarEntry, MealSlot

    return CalendarEntry(
        id=entry_id,
        date=date,
        recipe_name=f"Dish {entry_id}",
        ingredients=["pasta"],
        meal_slot=MealSlot.OBIAD,
        servings=4,
    )


@emulator_available
async def test_get_calendar_missing_doc_is_empty_not_an_error() -> None:
    svc = _make_service()
    cal = await svc.get_calendar("cal-user-never-saved")
    assert cal.entries == []
    assert cal.uid == "cal-user-never-saved"


@emulator_available
async def test_calendar_add_remove_round_trip() -> None:
    import uuid

    svc = _make_service()
    uid = f"cal-user-{uuid.uuid4().hex[:8]}"

    await svc.add_calendar_entry(uid, _cal_entry("e1"))
    await svc.add_calendar_entry(uid, _cal_entry("e2", date="2026-07-27"))
    cal = await svc.get_calendar(uid)
    assert [e.id for e in cal.entries] == ["e1", "e2"]
    # Fields survive the JSON round-trip through Firestore.
    assert cal.entries[0].servings == 4
    assert cal.entries[1].date == "2026-07-27"

    await svc.remove_calendar_entry(uid, "e1")
    assert [e.id for e in (await svc.get_calendar(uid)).entries] == ["e2"]


@emulator_available
async def test_add_calendar_entry_is_idempotent_on_id() -> None:
    """A re-sent add replaces the stored entry rather than duplicating it."""
    import uuid

    svc = _make_service()
    uid = f"cal-idem-{uuid.uuid4().hex[:8]}"

    await svc.add_calendar_entry(uid, _cal_entry("dup"))
    updated = _cal_entry("dup")
    updated.recipe_name = "Renamed"
    await svc.add_calendar_entry(uid, updated)

    cal = await svc.get_calendar(uid)
    assert len(cal.entries) == 1
    assert cal.entries[0].recipe_name == "Renamed"


@emulator_available
async def test_save_calendar_replaces_whole_state() -> None:
    """The shape PUT /v1/calendar relies on — drag/drop rewrites everything."""
    import uuid

    from cookbot.models.calendar import CalendarState

    svc = _make_service()
    uid = f"cal-save-{uuid.uuid4().hex[:8]}"

    await svc.add_calendar_entry(uid, _cal_entry("old"))
    await svc.save_calendar(CalendarState(uid=uid, entries=[_cal_entry("new")]))

    cal = await svc.get_calendar(uid)
    assert [e.id for e in cal.entries] == ["new"]


@emulator_available
async def test_list_user_records_returns_saved() -> None:
    from cookbot.models.user import TokenQuota, UserRecord

    svc = _make_service()
    await svc.save_user_record(
        UserRecord(uid="quota-list-a", quota=TokenQuota(daily_limit=1)),
    )
    records = await svc.list_user_records()
    assert any(r.uid == "quota-list-a" for r in records)
