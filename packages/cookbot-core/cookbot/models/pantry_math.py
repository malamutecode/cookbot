"""Pure pantry subtraction — take what the user already owns off a shopping list.

No Firestore, no LLM, no I/O. This runs AFTER the ShoppingListAgent has produced a
`ShoppingList`, so the agent keeps its single job (dedup/sum/section) and this
feature costs **zero extra tokens per turn**.

The central constraint is that `SpizarniaItem.quantity` is free text and is USUALLY
EMPTY — the pantry is an "I have flour" list, not an inventory. So there are two
regimes, and the difference matters more than the arithmetic:

    pantry amount known    → subtract it (200 g off 500 g → 300 g, or drop the
                             line entirely when the pantry covers the recipe)
    pantry amount unknown  → KEEP the line at full quantity and TAG it, so the
                             shopper decides in the aisle

Silently under-buying is worse than a redundant line, so anything ambiguous —
an unparseable amount ("2 duże cebule"), incompatible units (g vs szt.) — is left
completely untouched rather than guessed at.

Unit normalisation is delegated to `models.measures.convert_measure`; this module
never re-implements kitchen-measure arithmetic (see the note at the top of that
file for why the LLM must not do it either).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from cookbot.models.measures import convert_measure, fold_text
from cookbot.models.shopping import ShoppingItem, ShoppingList
from cookbot.models.spizarnia import SpizarniaItem

# Unit families. Only amounts within the same family can be subtracted from each
# other — "500 g mąki" minus "1 szt." is not arithmetic, it is a guess.
# Values are the multiplier to the family's base unit (g / ml / piece).
_MASS: dict[str, float] = {
    "g": 1.0, "gram": 1.0, "gramy": 1.0, "gramow": 1.0,
    "dag": 10.0, "deka": 10.0, "dkg": 10.0,
    "kg": 1000.0, "kilogram": 1000.0, "kilogramy": 1000.0,
}
_VOLUME: dict[str, float] = {
    "ml": 1.0, "mililitr": 1.0, "mililitry": 1.0,
    "cl": 10.0,
    "dl": 100.0,
    "l": 1000.0, "litr": 1000.0, "litry": 1000.0, "litrow": 1000.0,
}
_COUNT: dict[str, float] = {
    "szt": 1.0, "sztuka": 1.0, "sztuki": 1.0, "sztuk": 1.0,
    "opak": 1.0, "opakowanie": 1.0, "opakowania": 1.0,
    "pcs": 1.0, "pc": 1.0,
}

_FAMILIES: list[tuple[str, dict[str, float]]] = [
    ("mass", _MASS),
    ("volume", _VOLUME),
    ("count", _COUNT),
]

# Leading number: "1 1/2" | "1/2" | "1,5" | "1.5" | "2". Mirrors measures._parse_amount
# but returns a float, since shopping amounts don't need exact fractions.
_NUM_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s*/\s*(\d+)|^\s*(\d+)\s*/\s*(\d+)|^\s*(\d+)(?:[.,](\d+))?")


class PantryOutcome(StrEnum):
    """What happened to one shopping-list line."""

    UNTOUCHED = "untouched"  # no pantry match, or the amounts couldn't be compared
    REDUCED = "reduced"      # pantry covered part of it — quantity lowered
    COVERED = "covered"      # pantry covered all of it — line dropped from the list
    FLAGGED = "flagged"      # pantry has it but with no stated amount — kept + tagged


@dataclass(frozen=True)
class ParsedAmount:
    """A shopping/pantry amount reduced to a comparable (value, family) pair."""

    value: float          # in the family's base unit: g, ml, or pieces
    family: str           # "mass" | "volume" | "count"
    unit: str             # display unit the value is expressed in ("g", "ml", "szt.")


@dataclass(frozen=True)
class PantryAwareItem:
    """One line of the resulting list plus why it looks the way it does."""

    item: ShoppingItem
    outcome: PantryOutcome
    pantry_item: str = ""  # the pantry entry that matched, for logging/debugging


@dataclass(frozen=True)
class PantryAwareList:
    """The subtraction result. `shopping_list` holds only the lines still worth
    buying; `covered` names the items dropped, so a caller can tell the user why
    something disappeared instead of leaving it a silent edit."""

    shopping_list: ShoppingList
    results: list[PantryAwareItem]
    covered: list[str]


def _parse_number(text: str) -> float | None:
    m = _NUM_RE.match(text)
    if m is None:
        return None
    if m[1] is not None:  # mixed "1 1/2"
        den = int(m[3])
        return int(m[1]) + int(m[2]) / den if den else None
    if m[4] is not None:  # fraction "1/2"
        den = int(m[5])
        return int(m[4]) / den if den else None
    whole = m[6]
    frac = m[7]
    return float(f"{whole}.{frac}") if frac else float(whole)


def _unit_of(folded: str) -> tuple[str, float, str] | None:
    """Find the unit token in an already-folded amount string.

    Returns (family, multiplier-to-base, display-unit) or None. Matched as whole
    words so "l" in "cebula" is not read as litres.
    """
    for token in re.findall(r"[a-z]+", folded):
        for family, table in _FAMILIES:
            mult = table.get(token)
            if mult is not None:
                base = {"mass": "g", "volume": "ml", "count": "szt."}[family]
                return family, mult, base
    return None


def parse_amount(text: str) -> ParsedAmount | None:
    """Parse a free-text amount into a comparable value, or None when it can't be.

    Polish kitchen measures (szklanka/łyżka/łyżeczka) are normalised through
    `convert_measure` first, so "1 szklanka" and "250 ml" compare equal.

        parse_amount("500 g")       -> 500 g   (mass)
        parse_amount("1,5 kg")      -> 1500 g  (mass)
        parse_amount("1 szklanka")  -> 250 ml  (volume)
        parse_amount("2 duże cebule") -> None
    """
    if not text or not text.strip():
        return None
    converted = convert_measure(text)
    source = converted if converted is not None else text
    folded = fold_text(source)
    unit = _unit_of(folded)
    if unit is None:
        return None
    family, mult, display = unit
    number = _parse_number(folded)
    if number is None:
        # A bare unit with no number ("szklanka mąki") means one of it — but only
        # convert_measure knows that, and it already applied it above.
        return None
    return ParsedAmount(value=number * mult, family=family, unit=display)


def _fmt(value: float) -> str:
    """Render an amount without a trailing .0, keeping one decimal otherwise."""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _names_match(shopping_name: str, pantry_name: str) -> bool:
    """Diacritic- and case-insensitive containment either way.

    "Mąka" matches a pantry "maka"; a pantry "mąka pszenna" matches a list line
    "mąka". Containment (rather than equality) is what makes the free-text pantry
    usable at all, and it errs toward matching — the cost of a wrong match is a
    tag or a reduced line the user can see, not a silent deletion, because a drop
    only happens when both amounts parsed.
    """
    a = fold_text(shopping_name).strip()
    b = fold_text(pantry_name).strip()
    if not a or not b:
        return False
    return a in b or b in a


def _find_pantry_match(name: str, pantry: list[SpizarniaItem]) -> SpizarniaItem | None:
    """First pantry entry matching this shopping line, preferring one that states
    an amount — a quantified entry lets us subtract, an unquantified one can only
    flag."""
    matches = [p for p in pantry if _names_match(name, p.name)]
    if not matches:
        return None
    for p in matches:
        if parse_amount(p.quantity) is not None:
            return p
    return matches[0]


def subtract_pantry(
    shopping_list: ShoppingList,
    pantry: list[SpizarniaItem],
    *,
    note_have: str = "masz w spiżarni",
    note_check: str = "sprawdź w spiżarni",
) -> PantryAwareList:
    """Reduce a shopping list by what the pantry already holds.

    Never mutates its inputs (the pantry is read-only by design) and never drops a
    line unless both amounts parsed and the pantry genuinely covers it. An empty
    pantry is the identity function.

    `note_have` / `note_check` are the user-visible tags, passed in from
    `TenantConfig.ui` so this module stays language-agnostic.
    """
    if not pantry:
        return PantryAwareList(shopping_list=shopping_list, results=[], covered=[])

    results: list[PantryAwareItem] = []
    kept: list[ShoppingItem] = []
    covered: list[str] = []

    for item in shopping_list.items:
        match = _find_pantry_match(item.name, pantry)
        if match is None:
            results.append(PantryAwareItem(item=item, outcome=PantryOutcome.UNTOUCHED))
            kept.append(item)
            continue

        need = parse_amount(item.quantity)
        have = parse_amount(match.quantity)

        # Pantry amount unknown (the common case), or the two amounts aren't
        # comparable → keep the full quantity, tag it, let the shopper decide.
        if have is None or need is None or have.family != need.family:
            tagged = item.model_copy(update={"pantry_note": note_check})
            results.append(
                PantryAwareItem(item=tagged, outcome=PantryOutcome.FLAGGED, pantry_item=match.name)
            )
            kept.append(tagged)
            continue

        remaining = need.value - have.value
        if remaining <= 0:
            covered.append(item.name)
            results.append(
                PantryAwareItem(item=item, outcome=PantryOutcome.COVERED, pantry_item=match.name)
            )
            continue

        reduced = item.model_copy(update={
            "quantity": f"{_fmt(remaining)} {need.unit}",
            "pantry_note": note_have,
        })
        results.append(
            PantryAwareItem(item=reduced, outcome=PantryOutcome.REDUCED, pantry_item=match.name)
        )
        kept.append(reduced)

    sections = [s for s in shopping_list.sections if any(i.section == s for i in kept)]
    return PantryAwareList(
        shopping_list=ShoppingList(items=kept, sections=sections),
        results=results,
        covered=covered,
    )
