from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from cookbot.models.recipe import Recipe


class HITLCheckpoint(BaseModel):
    checkpoint_id: str
    session_id: str
    recipe: Recipe
    round_number: int
    created_at: datetime


class HITLResponse(BaseModel):
    approved: bool
    modification: str | None = None


class HITLOutcome(StrEnum):
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"
