from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WAITING_HITL = "WAITING_HITL"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"


class Session(BaseModel):
    session_id: str
    tenant_id: str
    uid: str | None = None
    messages: list[Message] = Field(default_factory=list)
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
