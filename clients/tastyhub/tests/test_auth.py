from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _make_decoded(uid: str = "user-123") -> dict:
    return {
        "uid": uid,
        "name": "Test User",
        "email": "test@example.com",
    }


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# get_current_user dependency tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_current_user_valid_token():
    from app.middleware.auth import get_current_user

    decoded = _make_decoded()
    with (
        patch("app.middleware.auth._get_firebase_app"),
        patch("firebase_admin.auth.verify_id_token", return_value=decoded),
    ):
        profile = await get_current_user(authorization="Bearer valid.token.here")

    assert profile.uid == "user-123"
    assert profile.email == "test@example.com"
    assert profile.display_name == "Test User"


@pytest.mark.asyncio
async def test_get_current_user_invalid_token():
    from fastapi import HTTPException

    from app.middleware.auth import get_current_user

    with (
        patch("app.middleware.auth._get_firebase_app"),
        patch("firebase_admin.auth.verify_id_token", side_effect=ValueError("bad token")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(authorization="Bearer bad.token")

    assert exc_info.value.status_code == 401
    assert "Invalid token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_current_user_expired_token():
    import firebase_admin.auth
    from fastapi import HTTPException

    from app.middleware.auth import get_current_user

    with (
        patch("app.middleware.auth._get_firebase_app"),
        patch(
            "firebase_admin.auth.verify_id_token",
            side_effect=firebase_admin.auth.ExpiredIdTokenError("expired", None),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(authorization="Bearer expired.token")

    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_get_current_user_missing_bearer_scheme():
    from fastapi import HTTPException

    from app.middleware.auth import get_current_user

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(authorization="Basic dXNlcjpwYXNz")

    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Access whitelist (allowed_emails) — enforced in get_current_user
# ---------------------------------------------------------------------------

def _patch_allowed_emails(emails: list[str]):
    """Patch the cached Settings.allowed_emails used by get_current_user."""
    from app.config.settings import get_settings

    return patch.object(get_settings(), "allowed_emails", emails)


@pytest.mark.asyncio
async def test_get_current_user_email_not_allowed_is_403():
    from fastapi import HTTPException

    from app.middleware.auth import get_current_user

    decoded = _make_decoded()  # email test@example.com
    with (
        _patch_allowed_emails(["allowed@example.com"]),
        patch("app.middleware.auth._get_firebase_app"),
        patch("firebase_admin.auth.verify_id_token", return_value=decoded),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(authorization="Bearer valid.token")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_current_user_email_allowed_passes():
    from app.middleware.auth import get_current_user

    decoded = _make_decoded()  # email test@example.com
    with (
        _patch_allowed_emails(["test@example.com"]),
        patch("app.middleware.auth._get_firebase_app"),
        patch("firebase_admin.auth.verify_id_token", return_value=decoded),
    ):
        profile = await get_current_user(authorization="Bearer valid.token")

    assert profile.email == "test@example.com"


@pytest.mark.asyncio
async def test_get_current_user_empty_whitelist_allows_all():
    from app.middleware.auth import get_current_user

    decoded = _make_decoded()
    with (
        _patch_allowed_emails([]),  # open
        patch("app.middleware.auth._get_firebase_app"),
        patch("firebase_admin.auth.verify_id_token", return_value=decoded),
    ):
        profile = await get_current_user(authorization="Bearer valid.token")

    assert profile.uid == "user-123"


# ---------------------------------------------------------------------------
# POST /v1/sessions — dual-auth tests
# ---------------------------------------------------------------------------

def test_create_session_with_api_key(client):
    resp = client.post("/v1/sessions", headers={"x-api-key": "tk_test_key"})
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data.get("uid") is None


def test_create_session_with_firebase_token(client):
    decoded = _make_decoded(uid="firebase-uid-abc")

    async def _noop_save(self, session):
        pass

    with (
        patch("app.api.sessions._get_firebase_app"),
        patch("firebase_admin.auth.verify_id_token", return_value=decoded),
        patch("cookbot.services.firestore.FirestoreService.save_session", new=_noop_save),
    ):
        resp = client.post(
            "/v1/sessions",
            headers={"authorization": "Bearer valid.firebase.token"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["uid"] == "firebase-uid-abc"
    assert "session_id" in data


def test_create_session_invalid_firebase_token(client):
    with (
        patch("app.api.sessions._get_firebase_app"),
        patch("firebase_admin.auth.verify_id_token", side_effect=ValueError("bad")),
    ):
        resp = client.post(
            "/v1/sessions",
            headers={"authorization": "Bearer bad.token"},
        )
    assert resp.status_code == 401


def test_create_session_no_auth(client):
    resp = client.post("/v1/sessions")
    assert resp.status_code == 401
