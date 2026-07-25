import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import firebase_admin.auth
from cookbot.models.session import Session, SessionStatus
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from app.config.settings import get_settings
from app.config.tenant import TASTYHUB_CONFIG
from app.middleware.auth import _get_firebase_app, record_is_locked

router = APIRouter()


class SessionCreateResponse(BaseModel):
    session_id: str
    expires_at: datetime
    uid: str | None = None


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> SessionCreateResponse:
    settings = get_settings()
    uid: str | None = None

    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        try:
            _get_firebase_app()
            decoded = firebase_admin.auth.verify_id_token(token)
            uid = decoded["uid"]
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
    elif x_api_key is not None:
        if not secrets.compare_digest(x_api_key, settings.api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: provide x-api-key or Authorization: Bearer <token>",
        )

    firestore = request.app.state.firestore

    # Temp-password gate (STEP 44). Applied to the already-verified uid rather
    # than via require_password_set, because this route also accepts the widget's
    # API key and has no Firebase identity in that case.
    if uid is not None and await record_is_locked(firestore, uid):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Password change required before using the app.",
        )

    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=settings.session_ttl_hours)
    session = Session(
        session_id=str(uuid4()),
        tenant_id=TASTYHUB_CONFIG.tenant_id,
        uid=uid,
        status=SessionStatus.ACTIVE,
        created_at=now,
        expires_at=expires_at,
    )
    await firestore.save_session(session)
    return SessionCreateResponse(
        session_id=session.session_id,
        expires_at=session.expires_at,
        uid=uid,
    )
