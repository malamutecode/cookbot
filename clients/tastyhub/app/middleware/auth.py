import secrets
from datetime import UTC, datetime

import firebase_admin
import firebase_admin.auth
from cookbot.models.tenant import TenantConfig
from cookbot.models.user import UserProfile, UserRecord
from fastapi import Depends, Header, HTTPException, Request, status

from app.config.settings import get_settings
from app.config.tenant import TASTYHUB_CONFIG

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


async def get_current_user(
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
    return UserProfile(
        uid=decoded["uid"],
        display_name=decoded.get("name", ""),
        email=decoded.get("email", ""),
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
    )


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
