"""REST surface for the server-side calendar (STEP 52).

Mirrors test_spizarnia.py: the service is stubbed with AsyncMock, so these cover
the route contract (auth gate, uid source, response shape), not Firestore itself
— that is exercised against the emulator in cookbot-core's test_firestore.py.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from cookbot.models.calendar import CalendarEntry, CalendarState, MealSlot
from cookbot.models.user import UserProfile, UserRecord
from fastapi.testclient import TestClient

from app.main import app

_UID = "user-cal-test"

_PROFILE = UserProfile(
    uid=_UID,
    display_name="Test",
    email="test@example.com",
    created_at=datetime.now(UTC),
)

_AUTH_HEADER = {"authorization": "Bearer valid.token"}


def _entry(entry_id: str, date: str = "2026-07-26", name: str = "Curry") -> CalendarEntry:
    return CalendarEntry(
        id=entry_id,
        date=date,
        recipe_name=name,
        ingredients=["ryż", "curry"],
        meal_slot=MealSlot.OBIAD,
    )


def _state(*ids: str) -> CalendarState:
    return CalendarState(uid=_UID, entries=[_entry(i) for i in ids])


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_auth():
    with (
        patch("app.middleware.auth._get_firebase_app"),
        patch(
            "firebase_admin.auth.verify_id_token",
            return_value={"uid": _UID, "name": "Test", "email": "test@example.com"},
        ),
        patch(
            "cookbot.services.firestore.FirestoreService.get_user_record",
            new=AsyncMock(return_value=UserRecord(uid=_UID, email="test@example.com")),
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# GET /v1/calendar
# ---------------------------------------------------------------------------

def test_get_calendar_empty(client, mock_auth):
    """A fresh user has no document — that is an empty plan, not a 404."""
    with patch.object(
        app.state.firestore, "get_calendar",
        new=AsyncMock(return_value=CalendarState(uid=_UID)),
    ):
        resp = client.get("/v1/calendar", headers=_AUTH_HEADER)

    assert resp.status_code == 200
    assert resp.json()["entries"] == []


def test_get_calendar_with_entries(client, mock_auth):
    with patch.object(
        app.state.firestore, "get_calendar", new=AsyncMock(return_value=_state("a", "b"))
    ):
        resp = client.get("/v1/calendar", headers=_AUTH_HEADER)

    assert resp.status_code == 200
    assert [e["id"] for e in resp.json()["entries"]] == ["a", "b"]


# ---------------------------------------------------------------------------
# PUT /v1/calendar
# ---------------------------------------------------------------------------

def test_put_calendar_round_trips(client, mock_auth):
    save = AsyncMock()
    with (
        patch.object(app.state.firestore, "save_calendar", new=save),
        patch.object(
            app.state.firestore, "get_calendar", new=AsyncMock(return_value=_state("a"))
        ),
    ):
        resp = client.put(
            "/v1/calendar",
            json={"entries": [_entry("a").model_dump(mode="json")]},
            headers=_AUTH_HEADER,
        )

    assert resp.status_code == 200
    assert [e["id"] for e in resp.json()["entries"]] == ["a"]
    assert save.await_count == 1


def test_put_calendar_uid_comes_from_token_not_body(client, mock_auth):
    """A client cannot write into someone else's plan by editing the payload."""
    save = AsyncMock()
    with (
        patch.object(app.state.firestore, "save_calendar", new=save),
        patch.object(
            app.state.firestore, "get_calendar", new=AsyncMock(return_value=_state())
        ),
    ):
        resp = client.put(
            "/v1/calendar",
            json={"uid": "somebody-else", "entries": []},
            headers=_AUTH_HEADER,
        )

    assert resp.status_code == 200
    assert save.await_args is not None
    saved: CalendarState = save.await_args.args[0]
    assert saved.uid == _UID


# ---------------------------------------------------------------------------
# DELETE /v1/calendar/entries/{entry_id}
# ---------------------------------------------------------------------------

def test_delete_entry_removes_by_id(client, mock_auth):
    remove = AsyncMock()
    with (
        patch.object(app.state.firestore, "remove_calendar_entry", new=remove),
        patch.object(
            app.state.firestore, "get_calendar", new=AsyncMock(return_value=_state("b"))
        ),
    ):
        resp = client.delete("/v1/calendar/entries/a", headers=_AUTH_HEADER)

    assert resp.status_code == 200
    assert [e["id"] for e in resp.json()["entries"]] == ["b"]
    assert remove.await_args is not None
    assert remove.await_args.args == (_UID, "a")


# ---------------------------------------------------------------------------
# Auth gates — all three routes, unauthenticated and temp-password-locked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/v1/calendar"),
        ("put", "/v1/calendar"),
        ("delete", "/v1/calendar/entries/a"),
    ],
)
def test_calendar_requires_auth(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/v1/calendar"),
        ("put", "/v1/calendar"),
        ("delete", "/v1/calendar/entries/a"),
    ],
)
def test_calendar_locked_while_password_unchanged(client, method, path):
    """423 until an admin-created account replaces its temp password (STEP 44)."""
    locked = UserRecord(uid=_UID, email="test@example.com", must_change_password=True)
    with (
        patch("app.middleware.auth._get_firebase_app"),
        patch(
            "firebase_admin.auth.verify_id_token",
            return_value={"uid": _UID, "name": "Test", "email": "test@example.com"},
        ),
        patch(
            "cookbot.services.firestore.FirestoreService.get_user_record",
            new=AsyncMock(return_value=locked),
        ),
    ):
        kwargs = {"json": {"entries": []}} if method == "put" else {}
        resp = getattr(client, method)(path, headers=_AUTH_HEADER, **kwargs)

    assert resp.status_code == 423
