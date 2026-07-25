import secrets
from datetime import UTC, datetime
from typing import Any

import firebase_admin
import firebase_admin.auth
import structlog
from cookbot.models.tenant import TenantConfig
from cookbot.models.user import UserProfile, UserRecord
from fastapi import Depends, Header, HTTPException, Request, status

from app.auth_policy import email_allowed
from app.config.settings import get_settings
from app.config.tenant import TASTYHUB_CONFIG

log = structlog.get_logger()

_firebase_app: firebase_admin.App | None = None


def _get_firebase_app() -> firebase_admin.App:
    global _firebase_app
    if _firebase_app is None:
        _firebase_app = firebase_admin.initialize_app()
    return _firebase_app


async def get_tenant_config(x_api_key: str = Header(...)) -> TenantConfig:
    if not secrets.compare_digest(x_api_key, get_settings().api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return TASTYHUB_CONFIG


async def record_grants_access(firestore: Any, uid: str) -> bool:
    """Firestore fallback for the access whitelist (STEP 44).

    An admin-created account must work without redeploying `ALLOWED_EMAILS`, so
    an existing, non-disabled `UserRecord` authorizes the caller on its own.
    Read-only on purpose: `get_user_record` would *create* a default record for
    any uid with a valid token, which would turn the whitelist into a no-op.

    Never raises — a Firestore hiccup means "no record", i.e. denied.
    """
    if firestore is None:
        return False
    try:
        rec = await firestore.find_user_record(uid)
    except Exception as exc:  # noqa: BLE001 — a lookup failure must mean "denied"
        log.warning("auth_record_lookup_failed", uid=uid, error=str(exc))
        return False
    # isinstance, not a truthiness check — see record_is_locked. Here the failure
    # mode is the opposite (a stub would *grant* access), so it matters more.
    return isinstance(rec, UserRecord) and not rec.disabled


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_dev_uid: str | None = Header(default=None),
) -> UserProfile:
    settings = get_settings()

    # Dev bypass: only active when DEV_UID is configured in settings
    if x_dev_uid and settings.dev_uid and x_dev_uid == settings.dev_uid:
        return UserProfile(
            uid=x_dev_uid,
            display_name="Dev User",
            email="dev@localhost",
            created_at=datetime.now(UTC),
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must use Bearer scheme",
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        _get_firebase_app()
        decoded = firebase_admin.auth.verify_id_token(token)
    except firebase_admin.auth.ExpiredIdTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    email = decoded.get("email", "")
    uid = decoded["uid"]
    # Access whitelist (empty ⇒ open). ALLOWED_EMAILS is a *bootstrap* list: an
    # admin-created account (STEP 44) has a Firestore UserRecord but is not on it,
    # so a False here falls through to the record lookup instead of refusing
    # outright. A valid token for an account we don't know is authenticated but
    # not authorized — 403, not 401.
    if not email_allowed(email, settings.allowed_emails):
        firestore = getattr(request.app.state, "firestore", None)
        if not await record_grants_access(firestore, uid):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account is not allowed to access the app.",
            )

    return UserProfile(
        uid=uid,
        display_name=decoded.get("name", ""),
        email=email,
        created_at=datetime.now(UTC),
    )


async def get_user_record(
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> UserRecord:
    """The caller's persisted account record (role + quota), creating a default
    on first sight and seeding admin from ADMIN_UIDS."""
    return await request.app.state.firestore.get_user_record(
        user.uid,
        default_quota=TASTYHUB_CONFIG.default_quota(),
        admin_uids=frozenset(TASTYHUB_CONFIG.admin_uids),
        email=user.email or None,
        display_name=user.display_name or None,
    )


async def require_password_set(
    record: UserRecord = Depends(get_user_record),
) -> UserRecord:
    """Gate product routes on the user having replaced their temp password.

    Returns 423 Locked while `must_change_password` is set. Enforcement lives
    here, not only in the UI — a hidden screen is not access control. The one
    route this must NOT gate is `POST /v1/me/password`, which depends on
    `get_user_record` directly so a locked user can still get out of the lock.
    """
    if record.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Password change required before using the app.",
        )
    return record


async def record_is_locked(firestore: Any, uid: str) -> bool:
    """True when `uid`'s record still carries `must_change_password` (STEP 44).

    The record-only half of `require_password_set`, for the entry points that
    resolve identity themselves rather than through `get_current_user`:
    `POST /v1/sessions` (also accepts the widget's API key) and the WebSocket
    handshake (no FastAPI dependency chain). Read-only — it never creates a
    record, and a lookup failure means "not locked" so a Firestore blip cannot
    lock everyone out.
    """
    if firestore is None:
        return False
    try:
        record = await firestore.find_user_record(uid)
    except Exception as exc:  # noqa: BLE001 — never break a route on a lookup blip
        log.warning("password_gate_lookup_failed", uid=uid, error=str(exc))
        return False
    # isinstance, not a truthiness check: a stubbed service can hand back a
    # non-record object whose every attribute is truthy, which would lock out
    # every caller. Only a real UserRecord may deny access.
    return isinstance(record, UserRecord) and record.must_change_password


async def require_admin(
    record: UserRecord = Depends(get_user_record),
) -> UserRecord:
    """Gate admin-only routes: the caller's own record must have role=='admin'."""
    if not record.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return record
