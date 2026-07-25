"""POST /v1/me/password + the require_password_set 423 gate (STEP 44).

The critical invariant under test: a locked user is refused by the product
routes but CAN still call /v1/me/password — otherwise a temp-password account
is locked out permanently.
"""

from unittest.mock import AsyncMock, patch

import firebase_admin.auth
import pytest
from cookbot.models.user import UserRecord
from fastapi.testclient import TestClient

from app.main import app

_UID = "locked-user"
_AUTH = {"authorization": "Bearer valid.token"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _mock_auth(uid: str = _UID):
    return patch("app.middleware.auth._get_firebase_app"), patch(
        "firebase_admin.auth.verify_id_token",
        return_value={"uid": uid, "email": f"{uid}@example.com"},
    )


def _record(*, locked: bool) -> UserRecord:
    return UserRecord(
        uid=_UID,
        email=f"{_UID}@example.com",
        must_change_password=locked,
    )


# ── POST /v1/me/password ──────────────────────────────────────────────────────

def test_change_password_clears_flag_and_calls_update_user(client):
    p1, p2 = _mock_auth()
    saved: dict = {}

    async def _save(rec: UserRecord) -> None:
        saved["rec"] = rec

    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(locked=True))),
        patch.object(app.state.firestore, "save_user_record", new=_save),
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.update_user") as update,
    ):
        resp = client.post(
            "/v1/me/password",
            json={"new_password": "noweHaslo123"},
            headers=_AUTH,
        )

    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is False
    update.assert_called_once_with(_UID, password="noweHaslo123")
    assert saved["rec"].must_change_password is False


def test_change_password_too_short_is_422(client):
    p1, p2 = _mock_auth()
    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(locked=True))),
        patch.object(app.state.firestore, "save_user_record", new=AsyncMock()) as save,
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.update_user") as update,
    ):
        resp = client.post("/v1/me/password", json={"new_password": "krotkie"}, headers=_AUTH)

    assert resp.status_code == 422
    # Polish copy from cookbot.models.password.validate_password.
    assert "8" in resp.json()["detail"]
    update.assert_not_called()
    save.assert_not_called()


def test_change_password_empty_is_422(client):
    p1, p2 = _mock_auth()
    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(locked=True))),
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.update_user") as update,
    ):
        resp = client.post("/v1/me/password", json={"new_password": ""}, headers=_AUTH)

    assert resp.status_code == 422
    update.assert_not_called()


def test_change_password_flag_not_cleared_when_firebase_rejects(client):
    """The flag must only clear once Firebase actually accepted the password."""
    p1, p2 = _mock_auth()
    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(locked=True))),
        patch.object(app.state.firestore, "save_user_record", new=AsyncMock()) as save,
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.update_user",
              side_effect=firebase_admin.auth.UserNotFoundError("gone", None, None)),
    ):
        resp = client.post("/v1/me/password", json={"new_password": "noweHaslo123"}, headers=_AUTH)

    assert resp.status_code == 404
    save.assert_not_called()


def test_change_password_requires_auth(client):
    resp = client.post("/v1/me/password", json={"new_password": "noweHaslo123"})
    assert resp.status_code == 401


# ── The 423 gate ──────────────────────────────────────────────────────────────

def test_locked_user_is_423_on_a_product_route(client):
    p1, p2 = _mock_auth()
    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(locked=True))),
        patch.object(app.state.firestore, "get_spizarnia", new=AsyncMock()),
    ):
        resp = client.get("/v1/spizarnia", headers=_AUTH)

    assert resp.status_code == 423


def test_locked_user_is_423_on_search_prefs(client):
    p1, p2 = _mock_auth()
    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(locked=True))),
        patch.object(app.state.firestore, "get_search_prefs", new=AsyncMock()),
    ):
        resp = client.get("/v1/search-prefs", headers=_AUTH)

    assert resp.status_code == 423


def test_locked_user_is_NOT_423_on_me_password(client):
    """The one route a password-locked user is allowed to call."""
    p1, p2 = _mock_auth()
    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(locked=True))),
        patch.object(app.state.firestore, "save_user_record", new=AsyncMock()),
        patch("app.api.admin._get_firebase_app"),
        patch("firebase_admin.auth.update_user"),
    ):
        resp = client.post("/v1/me/password", json={"new_password": "noweHaslo123"}, headers=_AUTH)

    assert resp.status_code == 200


def test_locked_user_can_still_read_me(client):
    """The SPA needs /v1/me to learn that it must show the change screen."""
    p1, p2 = _mock_auth()
    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(locked=True))),
    ):
        resp = client.get("/v1/me", headers=_AUTH)

    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is True


def test_unlocked_user_passes_the_gate(client):
    p1, p2 = _mock_auth()
    from cookbot.models.spizarnia import Spizarnia

    with (
        p1, p2,
        patch.object(app.state.firestore, "get_user_record",
                     new=AsyncMock(return_value=_record(locked=False))),
        patch.object(app.state.firestore, "get_spizarnia",
                     new=AsyncMock(return_value=Spizarnia(uid=_UID))),
    ):
        resp = client.get("/v1/spizarnia", headers=_AUTH)

    assert resp.status_code == 200


def test_locked_user_is_423_on_create_session(client):
    """/v1/sessions resolves identity itself, so it uses record_is_locked."""
    with (
        patch("app.api.sessions._get_firebase_app"),
        patch("firebase_admin.auth.verify_id_token",
              return_value={"uid": _UID, "email": f"{_UID}@example.com"}),
        patch.object(app.state.firestore, "find_user_record",
                     new=AsyncMock(return_value=_record(locked=True))),
        patch.object(app.state.firestore, "save_session", new=AsyncMock()) as save,
    ):
        resp = client.post("/v1/sessions", headers=_AUTH)

    assert resp.status_code == 423
    save.assert_not_called()
