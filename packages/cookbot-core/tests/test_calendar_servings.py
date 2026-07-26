"""Unit tests for portion-count bookkeeping on calendar entries (STEP 49).

The scaling maths itself is tested in `test_agents/test_recipe_scale.py`. What is
under test here is the *reporting* of that maths: whether a calendar entry can
state how many portions its ingredient list is for, and whether the three display
states (known / scaled / unknown) are decided in one place rather than being
re-derived by each UI surface.

All pure — no LLM, no I/O.
"""
from cookbot.models.calendar import (
    CalendarEntry,
    MealSlot,
    servings_are_known,
    servings_were_scaled,
)


def _entry(**over) -> CalendarEntry:
    base = dict(
        id="e1",
        date="2026-07-26",
        recipe_name="Curry z kurczaka",
        ingredients=["8 piersi z kurczaka", "500 ml śmietanki"],
    )
    base.update(over)
    return CalendarEntry(**base)


# ── servings_are_known ────────────────────────────────────────────────────────

def test_positive_servings_are_known() -> None:
    assert servings_are_known(1)
    assert servings_are_known(8)


def test_none_servings_are_unknown() -> None:
    """`None` is the "nobody ever told us" state — never render it as a number."""
    assert not servings_are_known(None)


def test_zero_servings_are_unknown() -> None:
    """A page that stated no serving count extracts as 0, not None.

    `scale_recipe_to_servings` treats `original <= 0` as "no anchor" and skips
    scaling, so 0 must read as unknown here too — otherwise the UI would print
    "Porcje: 0" above a list that was never adjusted.
    """
    assert not servings_are_known(0)


def test_negative_servings_are_unknown() -> None:
    assert not servings_are_known(-2)


# ── servings_were_scaled ──────────────────────────────────────────────────────

def test_scaled_when_source_differs() -> None:
    """Page served 4, entry is for 8 → the amounts were adjusted; say so."""
    assert servings_were_scaled(8, 4)


def test_not_scaled_when_source_matches() -> None:
    """The no-op case: user asked for exactly what the page serves."""
    assert not servings_were_scaled(4, 4)


def test_not_scaled_when_source_unknown() -> None:
    """No anchor means nothing was scaled — claiming otherwise would be a lie."""
    assert not servings_were_scaled(8, None)
    assert not servings_were_scaled(8, 0)


def test_not_scaled_when_servings_unknown() -> None:
    assert not servings_were_scaled(None, 4)
    assert not servings_were_scaled(0, 4)


# ── CalendarEntry fields ──────────────────────────────────────────────────────

def test_entry_records_servings_and_source() -> None:
    e = _entry(servings=8, source_servings=4)
    assert e.servings == 8
    assert e.source_servings == 4
    assert servings_are_known(e.servings)
    assert servings_were_scaled(e.servings, e.source_servings)


def test_legacy_entry_without_servings_parses() -> None:
    """Entries persisted before STEP 49 carry neither field.

    There is no migration step, so entries stored before the field existed keep
    being read after a deploy (localStorage until STEP 52, Firestore since).
    They must parse and read as "unknown" rather than raising — the same
    compatibility contract meal_slot got in STEP 48.
    """
    e = CalendarEntry.model_validate({
        "id": "legacy-1",
        "date": "2026-07-01",
        "recipe_name": "Rosół",
        "ingredients": ["kurczak", "marchewka"],
    })
    assert e.servings is None
    assert e.source_servings is None
    assert not servings_are_known(e.servings)
    assert e.meal_slot is MealSlot.OBIAD  # STEP 48 default still applies


def test_entry_roundtrips_servings_through_json() -> None:
    """The fields must survive the WS hop — WsOutCalendarUpdate nests the entry."""
    e = _entry(servings=8, source_servings=4)
    again = CalendarEntry.model_validate_json(e.model_dump_json())
    assert again.servings == 8
    assert again.source_servings == 4
