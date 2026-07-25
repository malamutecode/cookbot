"""Unit tests for pure pantry subtraction (STEP 51).

No LLM, no I/O — `subtract_pantry` is deliberately deterministic Python, so these
are direct calls with no TestModel involved.
"""

from cookbot.models.pantry_math import (
    PantryOutcome,
    parse_amount,
    subtract_pantry,
)
from cookbot.models.shopping import ShoppingItem, ShoppingList
from cookbot.models.spizarnia import SpizarniaItem

_HAVE = "masz w spiżarni"
_CHECK = "sprawdź w spiżarni"


def _list(*items: tuple[str, str]) -> ShoppingList:
    entries = [ShoppingItem(name=n, quantity=q, section="produkty suche/sypkie") for n, q in items]
    return ShoppingList(items=entries, sections=["produkty suche/sypkie"])


def _pantry(*items: tuple[str, str]) -> list[SpizarniaItem]:
    return [SpizarniaItem(name=n, quantity=q) for n, q in items]


def _run(sl: ShoppingList, pantry: list[SpizarniaItem]):
    return subtract_pantry(sl, pantry, note_have=_HAVE, note_check=_CHECK)


def _value(text: str) -> float:
    """parse_amount(...) that must succeed — asserts rather than returning None,
    so the test reads as a value comparison and pyright sees a float."""
    parsed = parse_amount(text)
    assert parsed is not None, f"expected {text!r} to parse"
    return parsed.value


# ── parse_amount ──────────────────────────────────────────────────────────────

def test_parse_amount_mass_units_normalise_to_grams():
    assert _value("500 g") == 500
    assert _value("1,5 kg") == 1500
    assert _value("2 dag") == 20


def test_parse_amount_volume_units_normalise_to_ml():
    assert _value("250 ml") == 250
    assert _value("1 l") == 1000


def test_parse_amount_uses_convert_measure_for_polish_kitchen_units():
    """"1 szklanka" must compare equal to "250 ml" — the whole point of routing
    through models.measures rather than re-implementing the table here."""
    szklanka = parse_amount("1 szklanka")
    ml = parse_amount("250 ml")
    assert szklanka is not None and ml is not None
    assert szklanka.family == ml.family == "volume"
    assert szklanka.value == ml.value == 250


def test_parse_amount_returns_none_for_unparseable_text():
    assert parse_amount("2 duże cebule") is None
    assert parse_amount("") is None
    assert parse_amount("szczypta") is None


def test_parse_amount_does_not_read_a_unit_out_of_a_word():
    """The "l" in "cebula" must not be read as litres — units match whole words."""
    assert parse_amount("3 cebula") is None


# ── the subtraction matrix ────────────────────────────────────────────────────

def test_partial_cover_reduces_the_quantity():
    result = _run(_list(("mąka", "500 g")), _pantry(("mąka", "200 g")))
    item = result.shopping_list.items[0]
    assert item.quantity == "300 g"
    assert item.pantry_note == _HAVE
    assert result.results[0].outcome is PantryOutcome.REDUCED


def test_full_cover_drops_the_item():
    result = _run(_list(("mąka", "200 g")), _pantry(("mąka", "500 g")))
    assert result.shopping_list.items == []
    assert result.covered == ["mąka"]
    assert result.results[0].outcome is PantryOutcome.COVERED


def test_exact_cover_drops_the_item():
    result = _run(_list(("mąka", "200 g")), _pantry(("mąka", "200 g")))
    assert result.shopping_list.items == []
    assert result.results[0].outcome is PantryOutcome.COVERED


def test_pantry_without_a_quantity_flags_and_keeps_the_full_amount():
    """The common case: the pantry is an "I have flour" list, not an inventory.
    Under-buying silently is worse than a redundant line."""
    result = _run(_list(("mąka", "500 g")), _pantry(("mąka", "")))
    item = result.shopping_list.items[0]
    assert item.quantity == "500 g"          # untouched
    assert item.pantry_note == _CHECK
    assert result.results[0].outcome is PantryOutcome.FLAGGED
    assert result.covered == []


def test_cross_unit_subtraction_via_convert_measure():
    """A recipe asking for 1 szklanka with 100 ml in the pantry leaves 150 ml."""
    result = _run(_list(("mleko", "1 szklanka")), _pantry(("mleko", "100 ml")))
    assert result.shopping_list.items[0].quantity == "150 ml"
    assert result.results[0].outcome is PantryOutcome.REDUCED


def test_unparseable_shopping_amount_is_flagged_not_guessed():
    result = _run(_list(("cebula", "2 duże cebule")), _pantry(("cebula", "1 szt.")))
    item = result.shopping_list.items[0]
    assert item.quantity == "2 duże cebule"  # left completely alone
    assert item.pantry_note == _CHECK
    assert result.results[0].outcome is PantryOutcome.FLAGGED


def test_incompatible_unit_families_are_flagged_not_subtracted():
    """500 g minus 1 szt. is not arithmetic — keep the full amount and tag it."""
    result = _run(_list(("masło", "500 g")), _pantry(("masło", "2 szt.")))
    assert result.shopping_list.items[0].quantity == "500 g"
    assert result.results[0].outcome is PantryOutcome.FLAGGED


def test_name_match_folds_diacritics_and_case():
    result = _run(_list(("Mąka", "500 g")), _pantry(("maka", "200 g")))
    assert result.shopping_list.items[0].quantity == "300 g"


def test_name_match_is_containment_both_ways():
    result = _run(_list(("mąka", "500 g")), _pantry(("mąka pszenna", "200 g")))
    assert result.shopping_list.items[0].quantity == "300 g"


def test_unmatched_items_are_untouched():
    result = _run(_list(("kurczak", "1 kg")), _pantry(("mąka", "200 g")))
    item = result.shopping_list.items[0]
    assert item.quantity == "1 kg"
    assert item.pantry_note == ""
    assert result.results[0].outcome is PantryOutcome.UNTOUCHED


def test_empty_pantry_is_the_identity():
    original = _list(("mąka", "500 g"), ("kurczak", "1 kg"))
    result = subtract_pantry(original, [])
    assert result.shopping_list is original
    assert result.covered == []


def test_a_quantified_pantry_entry_wins_over_an_unquantified_one():
    """Two entries match "mąka"; the one with an amount lets us actually subtract."""
    pantry = _pantry(("mąka", ""), ("mąka pszenna", "200 g"))
    result = _run(_list(("mąka", "500 g")), pantry)
    assert result.shopping_list.items[0].quantity == "300 g"


def test_sections_drop_when_every_item_in_them_was_covered():
    sl = ShoppingList(
        items=[
            ShoppingItem(name="mąka", quantity="200 g", section="produkty suche/sypkie"),
            ShoppingItem(name="kurczak", quantity="1 kg", section="mięso/ryby/wędliny"),
        ],
        sections=["produkty suche/sypkie", "mięso/ryby/wędliny"],
    )
    result = _run(sl, _pantry(("mąka", "500 g")))
    assert result.shopping_list.sections == ["mięso/ryby/wędliny"]


def test_inputs_are_never_mutated():
    """The pantry is read-only by design, and the caller may still need the
    pre-subtraction list."""
    original = _list(("mąka", "500 g"))
    pantry = _pantry(("mąka", "200 g"))
    _run(original, pantry)
    assert original.items[0].quantity == "500 g"
    assert original.items[0].pantry_note == ""
    assert pantry[0].quantity == "200 g"


def test_fractional_remainder_is_rendered_without_noise():
    result = _run(_list(("mleko", "1 l")), _pantry(("mleko", "250 ml")))
    assert result.shopping_list.items[0].quantity == "750 ml"
