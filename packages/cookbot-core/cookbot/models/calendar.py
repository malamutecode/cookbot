from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class MealSlot(StrEnum):
    """Meal section a calendar entry belongs to.

    Values are stable English keys, never user-facing text: they are persisted in
    the browser's localStorage, so renaming a Polish label must not invalidate
    saved plans. Display labels live in `ui_strings.py` / the frontend.
    """

    SNIADANIE = "sniadanie"
    LUNCH = "lunch"
    OBIAD = "obiad"
    KOLACJA = "kolacja"


class CalendarEntry(BaseModel):
    id: str
    date: str           # ISO date YYYY-MM-DD
    recipe_name: str
    ingredients: list[str]
    recipe: dict | None = None  # full Recipe JSON for detail modal; None for legacy entries
    # Defaulted so entries persisted before STEP 48 still parse; the frontend
    # applies the same fallback when reading slot-less localStorage data.
    meal_slot: MealSlot = MealSlot.OBIAD


class CalendarState(BaseModel):
    entries: list[CalendarEntry] = []
