from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.middleware.auth import get_tenant_config
from cookbot.models.session import Session, SessionStatus
from cookbot.models.tenant import TenantConfig

router = APIRouter()


class SessionCreateResponse(BaseModel):
    session_id: str
    expires_at: datetime


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session(
    request: Request,
    config: TenantConfig = Depends(get_tenant_config),
) -> SessionCreateResponse:
    firestore = request.app.state.firestore
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=request.app.state.settings.session_ttl_hours)
    session = Session(
        session_id=str(uuid4()),
        tenant_id=config.tenant_id,
        status=SessionStatus.ACTIVE,
        created_at=now,
        expires_at=expires_at,
    )
    await firestore.save_session(session)
    return SessionCreateResponse(
        session_id=session.session_id,
        expires_at=session.expires_at,
    )
