"""Admin API + require_admin gating (STEP 42)."""

from unittest.mock import AsyncMock, patch

import pytest
from cookbot.models.user import UsageCounter, UserRecord
from fastapi.testclient import TestClient

from app.main import app

_ADMIN_UID = "admin-uid"
_USER_UID = "plain-user"
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


# ── require_admin gating ──────────────────────────────────────────────────────

def test_list_users_forbidden_for_non_admin(client):
    p1, p2 = _mock_auth(_USER_UID)
    with p1, p2, patch.object(
        app.state.firestore, "get_user_record",
        new=AsyncMock(return_value=_record(_USER_UID, role="user")),
    ):
        resp = client.get("/v1/admin/users", headers=_AUTH)
    assert resp.status_code == 403


def test_list_users_ok_for_admin(client):
    p1, p2 = _mock_auth(_ADMIN_UID)
    records = [_record(_ADMIN_UID, role="admin"), _record(_USER_UID)]
    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(_ADMIN_UID, role="admin"))),
        patch.object(app.state.firestore, "list_user_records",
                     new=AsyncMock(return_value=records)),
        # Return the counter under the CURRENT period key so counter_for() keeps
        # it (a stale key would be lazily reset to 0).
        patch.object(app.state.firestore, "get_usage_counter",
                     new=AsyncMock(side_effect=lambda _uid, key: UsageCounter(period_key=key, tokens_used=42))),
    ):
        resp = client.get("/v1/admin/users", headers=_AUTH)
    assert resp.status_code == 200
    rows = resp.json()
    assert {r["record"]["uid"] for r in rows} == {_ADMIN_UID, _USER_UID}
    assert all(r["daily_used"] == 42 for r in rows)


# ── /me self-view ─────────────────────────────────────────────────────────────

def test_me_reports_admin_flag(client):
    p1, p2 = _mock_auth(_ADMIN_UID)
    with p1, p2, patch.object(
        app.state.firestore, "get_user_record",
        new=AsyncMock(return_value=_record(_ADMIN_UID, role="admin")),
    ):
        resp = client.get("/v1/me", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["is_admin"] is True


def test_me_non_admin(client):
    p1, p2 = _mock_auth(_USER_UID)
    with p1, p2, patch.object(
        app.state.firestore, "get_user_record",
        new=AsyncMock(return_value=_record(_USER_UID)),
    ):
        resp = client.get("/v1/me", headers=_AUTH)
    assert resp.json()["is_admin"] is False


# ── quota update ──────────────────────────────────────────────────────────────

def test_set_quota_saves_record(client):
    p1, p2 = _mock_auth(_ADMIN_UID)
    saved: dict = {}

    async def _save(rec: UserRecord) -> None:
        saved["rec"] = rec

    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(side_effect=lambda uid, **kw: _record(
                         uid, role="admin" if uid == _ADMIN_UID else "user"))),
        patch.object(app.state.firestore, "save_user_record", new=_save),
    ):
        resp = client.put(
            f"/v1/admin/users/{_USER_UID}/quota",
            json={"daily_limit": 1000, "monthly_limit": 25000},
            headers=_AUTH,
        )
    assert resp.status_code == 200
    assert resp.json()["quota"]["daily_limit"] == 1000
    assert saved["rec"].quota.monthly_limit == 25000


def test_set_role_rejects_bad_value(client):
    p1, p2 = _mock_auth(_ADMIN_UID)
    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(_ADMIN_UID, role="admin"))),
    ):
        resp = client.put(
            f"/v1/admin/users/{_USER_UID}/role",
            json={"role": "superuser"},
            headers=_AUTH,
        )
    assert resp.status_code == 422


def test_set_disabled_saves(client):
    p1, p2 = _mock_auth(_ADMIN_UID)
    saved: dict = {}

    async def _save(rec: UserRecord) -> None:
        saved["rec"] = rec

    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(side_effect=lambda uid, **kw: _record(
                         uid, role="admin" if uid == _ADMIN_UID else "user"))),
        patch.object(app.state.firestore, "save_user_record", new=_save),
    ):
        resp = client.put(
            f"/v1/admin/users/{_USER_UID}/disabled",
            json={"disabled": True},
            headers=_AUTH,
        )
    assert resp.status_code == 200
    assert saved["rec"].disabled is True


def test_admin_requires_auth(client):
    resp = client.get("/v1/admin/users")
    assert resp.status_code == 401
