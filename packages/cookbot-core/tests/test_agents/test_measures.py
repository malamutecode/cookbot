"""Unit tests for the deterministic Polish measure converter.

The whole point of this module is that the LLM must not do the arithmetic — so the
table values are asserted exactly against the reference conversion chart.
"""

from __future__ import annotations

import pytest

from cookbot.agents.measures import convert_measure


@pytest.mark.parametrize(
    "amount,expected",
    [
        # Exact table for fractional cups (the reported bug: 1/3 → 80 ml, not 150).
        ("1/4 szklanki", "60 ml"),
        ("1/3 szklanki", "80 ml"),
        ("1/2 szklanki", "125 ml"),
        ("2/3 szklanki", "160 ml"),
        ("3/4 szklanki", "180 ml"),
        ("1 szklanka", "250 ml"),
        ("szklanka mleka", "250 ml"),  # no number → one cup
        # Whole / decimal cups scale linearly from 250.
        ("2 szklanki", "500 ml"),
        ("1,5 szklanki", "375 ml"),
        # Tablespoons / teaspoons → ml by default.
        ("1 łyżka", "15 ml"),
        ("2 łyżki", "30 ml"),
        ("3 łyżeczki", "15 ml"),
        ("1 łyżeczka", "5 ml"),
        # Inflected forms fold to the same unit.
        ("5 łyżek oliwy", "75 ml"),
        ("48 łyżeczek", "240 ml"),
    ],
)
def test_convert_to_ml(amount: str, expected: str) -> None:
    assert convert_measure(amount) == expected


@pytest.mark.parametrize(
    "amount,expected",
    [
        ("1 łyżka", "15 g"),
        ("2 łyżki", "30 g"),
        ("1 łyżeczka", "5 g"),
        ("3 łyżeczki", "15 g"),
    ],
)
def test_convert_to_grams(amount: str, expected: str) -> None:
    assert convert_measure(amount, to_grams=True) == expected


@pytest.mark.parametrize(
    "amount",
    [
        "200 g mąki",       # already metric — nothing to convert
        "150 ml śmietanki",
        "2 jajka",
        "szczypta soli",
        "",
    ],
)
def test_no_convertible_unit_returns_none(amount: str) -> None:
    assert convert_measure(amount) is None


def test_lyzeczka_not_confused_with_lyzka() -> None:
    # "łyżeczka" contains the "łyż" stem — must resolve to teaspoon (5 ml), not
    # tablespoon (15 ml).
    assert convert_measure("1 łyżeczka") == "5 ml"
    assert convert_measure("1 łyżka") == "15 ml"


def test_mixed_number() -> None:
    assert convert_measure("1 1/2 szklanki") == "375 ml"
