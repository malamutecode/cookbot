"""Unit tests for the component-vs-standalone heuristic (STEP 45).

This is the whole decision the feature turns on, and it is a PURE FUNCTION — no
LLM, no network — so it is tested directly and exhaustively here rather than
through a live extraction. `tests/test_agents/test_chat.py` covers what the
ChatAgent does with the verdict; this file covers only the verdict itself.

The rule, in one line: a second ingredient block is STANDALONE (worth asking
about) when it declares its OWN serving count that differs from the main
recipe's, and its heading does not name a part of the main dish.

The bias is deliberate. Asking when we should have folded costs the user one
extra sentence; folding when we should have asked silently puts 500 g of flour
on a 4-person curry shopping list — the bug that motivated the step.
"""
from __future__ import annotations

from cookbot.models.recipe_blocks import (
    RecipeBlock,
    classify_blocks,
    has_standalone_blocks,
    page_declares_multiple_recipes,
    serving_headings,
)


def _block(name: str, servings: int = 0, n_ingredients: int = 5) -> RecipeBlock:
    return RecipeBlock(
        name=name,
        servings=servings,
        ingredients=[f"skladnik {i}" for i in range(n_ingredients)],
        steps=["Wymieszaj.", "Piecz."],
    )


# ── The motivating page ───────────────────────────────────────────────────────

def test_curry_and_naan_are_two_standalone_recipes() -> None:
    """The chilitonka page: "Składniki dla 4 osób" + "Składniki na 8 porcji".

    Two independent dishes under one URL. "8 porcji" on a block whose main
    recipe serves 4 is the signal that actually distinguished them — not the
    ingredient count, not the heading wording.
    """
    blocks = [
        _block("Curry z kurczaka", servings=4, n_ingredients=13),
        _block("Chlebek naan", servings=8, n_ingredients=8),
    ]

    verdict = classify_blocks(blocks, main_servings=4)

    assert verdict.standalone_names == ["Chlebek naan"]
    assert verdict.component_names == []
    assert has_standalone_blocks(blocks, main_servings=4)


# ── Components fold in silently ───────────────────────────────────────────────

def test_block_without_its_own_serving_count_is_a_component() -> None:
    """No serving count of its own → nothing to disambiguate, so fold it in.

    This is the common case on Polish recipe blogs: a "Sos" sub-header with four
    ingredients and no portion line. Today's behaviour already merges these, and
    the acceptance criteria require that to be unchanged.
    """
    blocks = [
        _block("Sałatka grecka", servings=4),
        _block("Sos winegret", servings=0),
    ]

    verdict = classify_blocks(blocks, main_servings=4)

    assert verdict.standalone_names == []
    assert verdict.component_names == ["Sos winegret"]
    assert not has_standalone_blocks(blocks, main_servings=4)


def test_matching_serving_count_is_a_component() -> None:
    """Same count as the main recipe → it is part of the same dish.

    A "Krem" block also marked "dla 4 osób" is the filling for the 4-person cake,
    not a second dessert. Asking here would be noise.
    """
    blocks = [
        _block("Tort", servings=4),
        _block("Krem", servings=4),
    ]

    verdict = classify_blocks(blocks, main_servings=4)

    assert verdict.component_names == ["Krem"]
    assert verdict.standalone_names == []


def test_component_heading_wins_even_with_its_own_serving_count() -> None:
    """A heading naming a part of the dish is decisive.

    "Sos na 8 porcji" alongside a 4-person main is a sauce recipe that happens to
    make extra — still a component of this dish, not a second dinner. The heading
    is stronger evidence than the number here, so it is checked first.
    """
    blocks = [
        _block("Pieczony kurczak", servings=4),
        _block("Sos czosnkowy", servings=8),
    ]

    verdict = classify_blocks(blocks, main_servings=4)

    assert verdict.component_names == ["Sos czosnkowy"]
    assert verdict.standalone_names == []


def test_all_component_headings_are_recognised() -> None:
    """The documented component vocabulary, each with a conflicting count.

    Listed explicitly so adding a word to the set requires adding it here too —
    the set is the feature's entire "don't ask" surface.
    """
    for heading in (
        "Sos", "Dressing", "Marynata", "Polewa", "Krem", "Farsz",
        "Nadzienie", "Dip", "Glazura", "Posypka",
    ):
        blocks = [_block("Danie główne", servings=4), _block(heading, servings=8)]
        verdict = classify_blocks(blocks, main_servings=4)
        assert verdict.component_names == [heading], f"{heading!r} should fold in"


def test_component_heading_matching_ignores_case_and_diacritics() -> None:
    """Headings are free text off a web page — "SOS CZOSNKOWY", "polewa".

    Folding is delegated to `models.measures.fold_text`, the one normalisation
    rule the codebase already uses for pantry matching.
    """
    blocks = [
        _block("Danie", servings=4),
        _block("POLEWA czekoladowa", servings=8),
    ]

    assert classify_blocks(blocks, main_servings=4).component_names == [
        "POLEWA czekoladowa"
    ]


def test_component_word_must_be_a_whole_word() -> None:
    """"Sos" folds; "Sosnowy chleb" does not.

    Naive substring matching would classify any heading containing "sos" as a
    sauce. The check is word-boundary based so a real dish is not swallowed.
    """
    blocks = [
        _block("Danie", servings=4),
        _block("Sosnowy chlebek", servings=8),
    ]

    verdict = classify_blocks(blocks, main_servings=4)

    assert verdict.standalone_names == ["Sosnowy chlebek"]


# ── The common path stays silent ──────────────────────────────────────────────

def test_single_block_never_asks() -> None:
    """One recipe on the page → no question, no extra work.

    The first acceptance criterion: the overwhelmingly common page must behave
    exactly as it does today.
    """
    assert not has_standalone_blocks([_block("Naleśniki", servings=4)], main_servings=4)
    assert not has_standalone_blocks([], main_servings=4)


def test_first_block_is_never_itself_standalone() -> None:
    """The main recipe is the anchor, not a candidate to split off.

    Guards an off-by-one: classifying the head block against itself would report
    a standalone on every single-recipe page and ask a pointless question.
    """
    verdict = classify_blocks(
        [_block("Curry", servings=4), _block("Naan", servings=8)], main_servings=4
    )

    assert "Curry" not in verdict.standalone_names
    assert "Curry" not in verdict.component_names


def test_unknown_main_servings_folds_everything() -> None:
    """Main recipe states no count → we cannot tell "different" from "same".

    With no anchor to compare against, splitting would be a guess. Fold in and
    stay silent: today's behaviour, which is never wrong in a new way.
    """
    blocks = [_block("Ciasto", servings=0), _block("Chlebek", servings=8)]

    verdict = classify_blocks(blocks, main_servings=0)

    assert verdict.standalone_names == []
    assert verdict.component_names == ["Chlebek"]


def test_a_block_with_no_ingredients_is_never_standalone() -> None:
    """An empty block is an extraction artefact, not a recipe.

    Splitting one off would produce a card with a name and nothing else.
    """
    empty = RecipeBlock(name="Chlebek naan", servings=8, ingredients=[], steps=[])

    verdict = classify_blocks([_block("Curry", servings=4), empty], main_servings=4)

    assert verdict.standalone_names == []


# ── Deterministic heading scan (the cross-check on the extractor) ─────────────

def test_serving_headings_finds_both_counts_on_the_real_page_markup() -> None:
    """The exact strings the live page produced, markdown emphasis included.

    This scanner exists because gpt-4o-mini reported `components=[]` for this page
    on a meaningful fraction of live turns while both headings sat plainly in the
    fetched text. Deterministic code reads them every time.
    """
    page = (
        "Jakiś wstęp o curry.\n\n"
        "**Składniki** **dla 4 osób:**\n- 4 piersi z kurczaka\n\n"
        "**Składniki na 8 porcji** :\n- 500 g mąki\n"
    )

    assert serving_headings(page) == [4, 8]
    assert page_declares_multiple_recipes(page)


def test_single_heading_page_is_not_flagged() -> None:
    """The common path must never trip the cross-check.

    One heading (or none) means the extractor's empty `components` is correct,
    so no retry fires and no tokens are spent.
    """
    assert serving_headings("Składniki dla 4 osób:\n- mąka") == [4]
    assert not page_declares_multiple_recipes("Składniki dla 4 osób:\n- mąka")
    assert not page_declares_multiple_recipes("Składniki:\n- mąka")
    assert not page_declares_multiple_recipes("")


def test_repeated_identical_heading_is_not_a_second_recipe() -> None:
    """A print view or summary box repeats the SAME count — not two dishes.

    Distinct counts are required, which is why this compares the set rather than
    the length: a duplicated "dla 4 osób" must stay silent.
    """
    page = "Składniki dla 4 osób:\n- x\n\nSkładniki dla 4 osób:\n- x\n"

    assert serving_headings(page) == [4, 4]
    assert not page_declares_multiple_recipes(page)


def test_heading_scan_survives_lost_diacritics() -> None:
    """Fetched text is not always clean UTF-8 — fold before matching."""
    assert serving_headings("Skladniki dla 4 osob:\n- x\n\nSkladniki na 8 porcji:\n- y") == [4, 8]


def test_three_blocks_are_classified_independently() -> None:
    """A page can carry a main dish, a sauce, and a second recipe at once."""
    blocks = [
        _block("Curry z kurczaka", servings=4),
        _block("Sos miętowy", servings=8),
        _block("Chlebek naan", servings=8),
    ]

    verdict = classify_blocks(blocks, main_servings=4)

    assert verdict.standalone_names == ["Chlebek naan"]
    assert verdict.component_names == ["Sos miętowy"]
