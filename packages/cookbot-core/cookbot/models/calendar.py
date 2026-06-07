from __future__ import annotations

from pydantic import BaseModel


class CalendarEntry(BaseModel):
    id: str
    date: str           # ISO date YYYY-MM-DD
    recipe_name: str
    ingredients: list[str]
    recipe: dict | None = None  # full Recipe JSON for detail modal; None for legacy entries


class CalendarState(BaseModel):
    entries: list[CalendarEntry] = []
