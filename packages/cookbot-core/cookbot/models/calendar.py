from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MealSlot(StrEnum):
    """Meal section a calendar entry belongs to.

    Values are stable English keys, never user-facing text: they are persisted in
    Firestore under `users/{uid}/calendar/entries`, so renaming a Polish label
    must not invalidate saved plans. Display labels live in `ui_strings.py` /
    the frontend.
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
    # applies the same fallback when reading slot-less stored data.
    meal_slot: MealSlot = MealSlot.OBIAD
    # How many portions `ingredients` is for (STEP 49). The quantities above are
    # already scaled to this count by RecipeScaleAgent at resolve time — this
    # field is what lets the UI SAY so instead of showing a bare, unverifiable
    # number. `source_servings` is the count the source page stated, kept so the
    # UI can show "8 porcji (przeliczone z 4)".
    #
    # Both default to None ("unknown") so entries persisted before STEP 49 still
    # parse — the same compatibility contract as meal_slot.
    servings: int | None = None
    source_servings: int | None = None


class CalendarState(BaseModel):
    """A user's whole meal plan — one Firestore doc at `users/{uid}/calendar/entries`.

    `uid` is defaulted (unlike `Spizarnia.uid`) because the chat flow builds an
    empty `CalendarState()` for anonymous connections, which own no document and
    never reach `save_calendar`.
    """

    uid: str = ""
    entries: list[CalendarEntry] = []
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── Portion-count display rules (pure; no I/O) ────────────────────────────────
# Kept here rather than in the UI so the tool, the tests and both frontend
# surfaces agree on what "unknown" and "scaled" mean. Mirrored in
# frontend/src/lib/servings.ts.

def servings_are_known(servings: int | None) -> bool:
    """True when a portion count is real and safe to display.

    `0` is the extractor's "the page never said" value — `scale_recipe_to_servings`
    treats `original <= 0` as having no anchor and skips scaling — so 0 must read
    as unknown here too, never render as "Porcje: 0".
    """
    return servings is not None and servings > 0


def servings_were_scaled(servings: int | None, source_servings: int | None) -> bool:
    """True when quantities were converted from a different source count.

    Requires both counts to be known and to differ. Claiming a conversion without
    a real anchor would tell the user something we cannot back up.
    """
    if not servings_are_known(servings) or not servings_are_known(source_servings):
        return False
    return servings != source_servings
