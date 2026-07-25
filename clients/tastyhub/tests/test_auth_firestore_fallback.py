"""ALLOWED_EMAILS is a *bootstrap* whitelist (STEP 44).

An admin-created account has a Firestore UserRecord but is not on the env
whitelist, and must work without a redeploy. `get_current_user` therefore falls
back to a read-only record lookup when `email_allowed` returns False.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from cookbot.models.user import UserRecord
from fastapi import HTTPException

from app.middleware.auth import get_current_user

_UID = "created-by-admin"
_EMAIL = "test@example.com"  # deliberately NOT on the whitelist below


def _decoded() -> dict:
    return {"uid": _UID, "name": "Nowy", "email": _EMAIL}


def _req(firestore=None):
    """Minimal stand-in Request — only app.state.firestore is touched."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(firestore=firestore)))


def _firestore(record: UserRecord | None) -> AsyncMock:
    fs = AsyncMock()
    fs.find_user_record = AsyncMock(return_value=record)
    return fs


def _patch_allowed_emails(emails: list[str]):
    from app.config.settings import get_settings

    return patch.object(get_settings(), "allowed_emails", emails)


def _mock_token():
    return patch("app.middleware.auth._get_firebase_app"), patch(
        "firebase_admin.auth.verify_id_token", return_value=_decoded()
    )


# ── The fallback ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_offlist_email_with_existing_record_is_allowed():
    p1, p2 = _mock_token()
    fs = _firestore(UserRecord(uid=_UID, email=_EMAIL))
    with _patch_allowed_emails(["someone-else@example.com"]), p1, p2:
        profile = await get_current_user(_req(fs), authorization="Bearer valid.token")

    assert profile.uid == _UID
    fs.find_user_record.assert_awaited_once_with(_UID)


@pytest.mark.asyncio
async def test_offlist_email_with_disabled_record_is_403():
    p1, p2 = _mock_token()
    fs = _firestore(UserRecord(uid=_UID, email=_EMAIL, disabled=True))
    with _patch_allowed_emails(["someone-else@example.com"]), p1, p2:
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_req(fs), authorization="Bearer valid.token")

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_offlist_email_with_no_record_is_403():
    p1, p2 = _mock_token()
    fs = _firestore(None)
    with _patch_allowed_emails(["someone-else@example.com"]), p1, p2:
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_req(fs), authorization="Bearer valid.token")

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_lookup_uses_find_not_get_so_it_never_creates_a_record():
    """`get_user_record` creates a default on first sight — using it here would
    turn any uid with a valid token into an authorized one."""
    p1, p2 = _mock_token()
    fs = _firestore(None)
    with _patch_allowed_emails(["someone-else@example.com"]), p1, p2:
        with pytest.raises(HTTPException):
            await get_current_user(_req(fs), authorization="Bearer valid.token")

    fs.get_user_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_firestore_failure_denies_rather_than_crashing():
    p1, p2 = _mock_token()
    fs = AsyncMock()
    fs.find_user_record = AsyncMock(side_effect=RuntimeError("firestore down"))
    with _patch_allowed_emails(["someone-else@example.com"]), p1, p2:
        with pytest.raises(HTTPException) as exc:
            await get_current_user(_req(fs), authorization="Bearer valid.token")

    assert exc.value.status_code == 403


# ── The fallback must not fire when the whitelist already passed ──────────────

@pytest.mark.asyncio
async def test_whitelisted_email_skips_the_firestore_lookup():
    p1, p2 = _mock_token()
    fs = _firestore(None)
    with _patch_allowed_emails([_EMAIL]), p1, p2:
        profile = await get_current_user(_req(fs), authorization="Bearer valid.token")

    assert profile.uid == _UID
    fs.find_user_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_whitelist_skips_the_firestore_lookup():
    """Empty ALLOWED_EMAILS still means open sign-in — unchanged behaviour."""
    p1, p2 = _mock_token()
    fs = _firestore(None)
    with _patch_allowed_emails([]), p1, p2:
        profile = await get_current_user(_req(fs), authorization="Bearer valid.token")

    assert profile.uid == _UID
    fs.find_user_record.assert_not_awaited()
