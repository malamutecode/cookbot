"""Deterministic Polish cooking-measure conversion.

The LLM must NOT do this arithmetic itself — it got "1/3 szklanki" wrong (returned
150 ml instead of the correct 80 ml). This module converts volumetric kitchen
units (szklanka, łyżka, łyżeczka) to millilitres, and (for the tablespoon/teaspoon
that the reference table gives in grams) to grams, using a fixed table:

    1 szklanka   = 250 ml     1 łyżka    = 15 ml (≈ 15 g)
    3/4 szklanki = 180 ml     1 łyżeczka =  5 ml (≈  5 g)
    2/3 szklanki = 160 ml
    1/2 szklanki = 125 ml
    1/3 szklanki =  80 ml
    1/4 szklanki =  60 ml

Fractional szklanka values use the table's exact figures (they are rounded to
common kitchen amounts, not simple multiples of 250). Other amounts scale linearly
from the per-unit base. It is exposed to the shopping-list agent as a tool so the
model normalises amounts by CALLING this code rather than guessing.
"""

from __future__ import annotations

import re
import unicodedata
from fractions import Fraction

# Exact table for the common fractional cup amounts (ml). Keyed by Fraction so
# "1/3" maps to 80, not 250/3≈83.
_SZKLANKA_TABLE: dict[Fraction, int] = {
    Fraction(1, 4): 60,
    Fraction(1, 3): 80,
    Fraction(1, 2): 125,
    Fraction(2, 3): 160,
    Fraction(3, 4): 180,
    Fraction(1, 1): 250,
}

# Per-unit base values for linear scaling when the amount isn't a tabled fraction.
_SZKLANKA_ML = 250.0
_LYZKA_ML = 15.0
_LYZECZKA_ML = 5.0
_LYZKA_G = 15.0
_LYZECZKA_G = 5.0

# Unit word (ascii-folded stem) → kind. Polish inflections all fold to a common
# stem. Order matters: łyżeczka forms (lyzecz*) are checked BEFORE łyżka (lyz*),
# since "łyżeczka" also starts with "łyż".
_UNIT_STEMS = (
    ("szklan", "szklanka"),   # szklanka, szklanki, szklanek
    ("lyzecz", "lyzeczka"),   # łyżeczka, łyżeczki, łyżeczek
    ("lyz", "lyzka"),         # łyżka, łyżki, łyżek
)


def fold_text(text: str) -> str:
    """Strip Polish diacritics and lowercase, so "Mąka"/"maka"/"MĄKA" compare equal.

    Public because pantry matching (`models/pantry_math.py`) needs exactly this
    normalisation — one folding rule for the whole codebase, not two that drift.
    """
    # "ł"/"Ł" do NOT decompose to ASCII "l" under NFKD (they're distinct letters),
    # so map them first — otherwise "łyżka" would fold to "yzka" and never match.
    text = text.replace("ł", "l").replace("Ł", "L")
    decomposed = unicodedata.normalize("NFKD", text)
    return decomposed.encode("ascii", "ignore").decode("ascii").lower()


_fold = fold_text  # module-internal alias (historic name)


def _parse_amount(token_stream: str) -> Fraction | None:
    """Parse a leading quantity: fraction "1/3", mixed "1 1/2", decimal "1,5"/"1.5",
    or whole "2". Returns None if no numeric amount is present."""
    s = token_stream.strip()
    # Mixed number: "1 1/2"
    m = re.match(r"^(\d+)\s+(\d+)\s*/\s*(\d+)", s)
    if m:
        whole, num, den = int(m[1]), int(m[2]), int(m[3])
        if den == 0:
            return None
        return whole + Fraction(num, den)
    # Simple fraction: "1/3"
    m = re.match(r"^(\d+)\s*/\s*(\d+)", s)
    if m:
        num, den = int(m[1]), int(m[2])
        if den == 0:
            return None
        return Fraction(num, den)
    # Decimal (comma or dot) or whole: "1,5" / "1.5" / "2"
    m = re.match(r"^(\d+)(?:[.,](\d+))?", s)
    if m:
        whole = m[1]
        frac = m[2] or ""
        return Fraction(f"{whole}.{frac}") if frac else Fraction(int(whole))
    return None


def _find_unit(text: str) -> str | None:
    folded = _fold(text)
    for stem, kind in _UNIT_STEMS:
        if stem in folded:
            return kind
    return None


def _fmt(value: float) -> str:
    """Render a number without a trailing .0, keeping one decimal otherwise."""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def convert_measure(amount: str, *, to_grams: bool = False) -> str | None:
    """Convert a Polish volumetric amount to ml (or g for łyżka/łyżeczka).

    Returns the normalised string (e.g. "80 ml", "30 g") or None when the input
    has no szklanka/łyżka/łyżeczka unit to convert (caller keeps it unchanged).

    Examples:
        convert_measure("1/3 szklanki")        -> "80 ml"
        convert_measure("2 łyżki")             -> "30 ml"
        convert_measure("2 łyżki", to_grams=True) -> "30 g"
        convert_measure("200 g mąki")          -> None  (nothing to convert)
    """
    unit = _find_unit(amount)
    if unit is None:
        return None
    qty = _parse_amount(amount)
    if qty is None:
        qty = Fraction(1)  # "szklanka mąki" with no number means one

    if unit == "szklanka":
        ml = _SZKLANKA_TABLE.get(qty)
        if ml is None:
            ml = float(qty) * _SZKLANKA_ML
        return f"{_fmt(float(ml))} ml"
    if unit == "lyzka":
        base = _LYZKA_G if to_grams else _LYZKA_ML
        return f"{_fmt(float(qty) * base)} {'g' if to_grams else 'ml'}"
    if unit == "lyzeczka":
        base = _LYZECZKA_G if to_grams else _LYZECZKA_ML
        return f"{_fmt(float(qty) * base)} {'g' if to_grams else 'ml'}"
    return None
