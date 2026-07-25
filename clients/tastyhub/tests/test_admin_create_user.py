"""Admin-created user accounts — POST/DELETE /v1/admin/users (STEP 44).

Hermetic: `firebase_admin.auth` is patched and the Firestore service is an
AsyncMock. No real Firebase, no network, no emulator.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import firebase_admin.auth
import pytest
from cookbot.models.user import UserRecord
from fastapi.testclient import TestClient

from app.main import app

_ADMIN_UID = "admin-uid"
_USER_UID = "plain-user"
_NEW_UID = "brand-new-uid"
_AUTH = {"authorization": "Bearer valid.token"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _mock_auth(uid: str):
    return patch("app.middleware.auth._get_firebase_app"), patch(
        "firebase_admin.auth.verify_id_token",
        return_value={"uid": uid, "email": f"{uid}@example.com"},
    )


def _record(uid: str, role: str = "user", **kw) -> UserRecord:
    return UserRecord(uid=uid, email=f"{uid}@example.com", role=role, **kw)


def _as_admin(uid: str = _ADMIN_UID):
    """Resolve the *caller's* record as an admin; anyone else as a plain user."""
    return patch.object(
        app.state.firestore,
        "get_user_record",
        new=AsyncMock(side_effect=lambda u, **kw: _record(u, role="admin" if u == uid else "user")),
    )


# ── POST /v1/admin/users ──────────────────────────────────────────────────────

def test_create_returns_temp_password_and_flagged_record(client):
    p1, p2 = _mock_auth(_ADMIN_UID)
    saved: dict = {}

    async def _save(rec: UserRecord) -> None:
        saved["rec"] = rec

    with (
        p1, p2, _as_admin(),
        patch.object(app.state.firestore, "save_user_record", new=_save),
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.create_user",
              return_value=SimpleNamespace(uid=_NEW_UID)) as create,
    ):
        resp = client.post(
            "/v1/admin/users",
            json={
                "email": "nowy@example.com",
                "display_name": "Nowy User",
                "role": "user",
                "daily_limit": 1000,
                "monthly_limit": 25000,
            },
            headers=_AUTH,
        )

    assert resp.status_code == 201
    body = resp.json()
    # The password crosses the wire exactly once, and it is a real generated one.
    assert len(body["temp_password"]) == 12
    assert any(c.isdigit() for c in body["temp_password"])
    assert body["record"]["uid"] == _NEW_UID
    assert body["record"]["must_change_password"] is True
    assert body["record"]["display_name"] == "Nowy User"
    assert body["record"]["quota"]["daily_limit"] == 1000

    # The Firebase account was created with that same password.
    kwargs = create.call_args.kwargs
    assert kwargs["email"] == "nowy@example.com"
    assert kwargs["password"] == body["temp_password"]
    assert kwargs["display_name"] == "Nowy User"

    # And the record persisted carries the flag (server-side enforcement source).
    assert saved["rec"].must_change_password is True


def test_create_defaults_quota_to_unlimited(client):
    """Quota convention: 0 ⇒ unlimited."""
    p1, p2 = _mock_auth(_ADMIN_UID)
    with (
        p1, p2, _as_admin(),
        patch.object(app.state.firestore, "save_user_record", new=AsyncMock()),
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.create_user", return_value=SimpleNamespace(uid=_NEW_UID)),
    ):
        resp = client.post("/v1/admin/users", json={"email": "a@b.com"}, headers=_AUTH)

    assert resp.status_code == 201
    assert resp.json()["record"]["quota"] == {"daily_limit": 0, "monthly_limit": 0}


def test_create_duplicate_email_is_409(client):
    p1, p2 = _mock_auth(_ADMIN_UID)
    with (
        p1, p2, _as_admin(),
        patch.object(app.state.firestore, "save_user_record", new=AsyncMock()) as save,
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.create_user",
              side_effect=firebase_admin.auth.EmailAlreadyExistsError("dup", None, None)),
    ):
        resp = client.post("/v1/admin/users", json={"email": "istnieje@example.com"}, headers=_AUTH)

    assert resp.status_code == 409
    # A live user's password must never be silently reset — nothing was written.
    save.assert_not_called()


def test_create_rejects_malformed_email(client):
    p1, p2 = _mock_auth(_ADMIN_UID)
    with (
        p1, p2, _as_admin(),
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.create_user") as create,
    ):
        resp = client.post("/v1/admin/users", json={"email": "not-an-email"}, headers=_AUTH)

    assert resp.status_code == 422
    create.assert_not_called()


def test_create_rejects_bad_role(client):
    p1, p2 = _mock_auth(_ADMIN_UID)
    with (
        p1, p2, _as_admin(),
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.create_user") as create,
    ):
        resp = client.post(
            "/v1/admin/users",
            json={"email": "a@b.com", "role": "superuser"},
            headers=_AUTH,
        )

    assert resp.status_code == 422
    create.assert_not_called()


def test_create_forbidden_for_non_admin(client):
    p1, p2 = _mock_auth(_USER_UID)
    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(_USER_UID, role="user"))),
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.create_user") as create,
    ):
        resp = client.post("/v1/admin/users", json={"email": "a@b.com"}, headers=_AUTH)

    assert resp.status_code == 403
    create.assert_not_called()


def test_create_requires_auth(client):
    resp = client.post("/v1/admin/users", json={"email": "a@b.com"})
    assert resp.status_code == 401


# ── DELETE /v1/admin/users/{uid} ──────────────────────────────────────────────

def test_delete_removes_firebase_user_and_record(client):
    p1, p2 = _mock_auth(_ADMIN_UID)
    with (
        p1, p2, _as_admin(),
        patch.object(app.state.firestore, "delete_user_record", new=AsyncMock()) as drop,
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.delete_user") as fb_delete,
    ):
        resp = client.delete(f"/v1/admin/users/{_USER_UID}", headers=_AUTH)

    assert resp.status_code == 204
    fb_delete.assert_called_once_with(_USER_UID)
    drop.assert_awaited_once_with(_USER_UID)


def test_delete_self_is_409(client):
    p1, p2 = _mock_auth(_ADMIN_UID)
    with (
        p1, p2, _as_admin(),
        patch.object(app.state.firestore, "delete_user_record", new=AsyncMock()) as drop,
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.delete_user") as fb_delete,
    ):
        resp = client.delete(f"/v1/admin/users/{_ADMIN_UID}", headers=_AUTH)

    assert resp.status_code == 409
    fb_delete.assert_not_called()
    drop.assert_not_called()


def test_delete_drops_record_even_if_firebase_user_is_gone(client):
    """An orphaned record must still be cleanable from the panel."""
    p1, p2 = _mock_auth(_ADMIN_UID)
    with (
        p1, p2, _as_admin(),
        patch.object(app.state.firestore, "delete_user_record", new=AsyncMock()) as drop,
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.delete_user",
              side_effect=firebase_admin.auth.UserNotFoundError("gone", None, None)),
    ):
        resp = client.delete(f"/v1/admin/users/{_USER_UID}", headers=_AUTH)

    assert resp.status_code == 204
    drop.assert_awaited_once_with(_USER_UID)


def test_delete_forbidden_for_non_admin(client):
    p1, p2 = _mock_auth(_USER_UID)
    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(_USER_UID, role="user"))),
        patch.object(app.state.firestore, "delete_user_record", new=AsyncMock()) as drop,
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.delete_user") as fb_delete,
    ):
        resp = client.delete(f"/v1/admin/users/{_ADMIN_UID}", headers=_AUTH)

    assert resp.status_code == 403
    fb_delete.assert_not_called()
    drop.assert_not_called()
