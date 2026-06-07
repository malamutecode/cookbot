from datetime import UTC, datetime

from pydantic import BaseModel, Field


class SpizarniaItem(BaseModel):
    name: str
    quantity: str = ""
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Spizarnia(BaseModel):
    uid: str
    items: list[SpizarniaItem] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
