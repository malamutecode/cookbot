"""Component-vs-standalone classification for multi-recipe pages (STEP 45).

Some recipe pages host **two independent recipes** under one URL. The motivating
case is a chilitonka post carrying a curry ("Składniki dla 4 osób") and a naan
bread ("Składniki na 8 porcji"). Extraction is verbatim (agents/CLAUDE.md Rule 5),
so it correctly captures both — and produces one Recipe with 21 ingredients whose
`servings=4`. A 4-person curry then silently buys 8 portions of naan.

That is **not an extraction bug**: the page really does contain both. The product
question is which the user wants, and only the user can answer it. This module
decides one narrower thing — *is the extra block even worth asking about?*

    STANDALONE → a dish that could be cooked alone   → ask the user
    COMPONENT  → a part of the main dish (sos, krem) → fold in silently

Pure Python, no LLM, no I/O — the same reasoning as `models/pantry_math.py`: a
decision this mechanical must never become prompt instructions, because a model
asked to judge it will occasionally split a sauce off into its own "recipe".

The bias is deliberate. Asking when we should have folded costs one extra
sentence; folding when we should have asked puts 500 g of flour on a curry
shopping list — the bug this step exists to fix. But the *default* is to fold:
every ambiguous signal (no serving count, no anchor to compare against, an empty
block) resolves to COMPONENT, which is exactly today's behaviour and therefore
never wrong in a NEW way.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from cookbot.models.measures import fold_text

# Headings that name a PART of the main dish. A block titled with one of these is
# a component no matter what serving count it declares: "Sos na 8 porcji" next to
# a 4-person main is a sauce that makes extra, not a second dinner.
#
# Diacritic-folded (fold_text) because these are matched against folded headings —
# "Polewa" and "POLEWA" and "polewa" must all hit. Adding a word here widens the
# "don't ask" surface, so `tests/test_recipe_blocks.py` pins the list explicitly.
_COMPONENT_WORDS: frozenset[str] = frozenset({
    "sos", "sosy",
    "dressing",
    "marynata",
    "polewa", "lukier",
    "krem",
    "farsz", "nadzienie",
    "dip",
    "glazura",
    "posypka", "kruszonka",
})


class RecipeBlock(BaseModel):
    """One ingredient/step block as the extractor SAW it on the page.

    Reported verbatim, never judged, by the fetch/search agents — the extractor's
    only new job is to say "the page had these blocks" (Rule 5: extraction
    reports, it does not decide). `servings=0` means the block declared no count
    of its own, which is the common case for a sauce sub-header.
    """

    name: str
    servings: int = 0
    ingredients: list[str] = []
    steps: list[str] = []


class BlockVerdict(BaseModel):
    """How each non-main block was classified. Names, in page order."""

    standalone_names: list[str] = []
    component_names: list[str] = []


def _is_component_heading(name: str) -> bool:
    """Does this heading name a part of a dish rather than a dish?

    Word-boundary matched, not substring: a naive `"sos" in name` would fold
    "Sosnowy chlebek" into the main recipe.
    """
    folded = fold_text(name)
    words = set(re.findall(r"[a-z]+", folded))
    return bool(words & _COMPONENT_WORDS)


def classify_blocks(blocks: list[RecipeBlock], main_servings: int) -> BlockVerdict:
    """Split the page's blocks into "ask about these" and "fold these in".

    `blocks[0]` is the MAIN recipe and is never classified — it is the anchor the
    others are compared against, not a candidate to split off. (Classifying it
    against itself would report a standalone on every single-recipe page.)

    A block is standalone only when all of these hold:
      1. its heading does not name a component (sos / krem / marynata / …),
      2. it declares its OWN serving count,
      3. that count differs from the main recipe's,
      4. the main recipe has a serving count to compare against, and
      5. it actually has ingredients.

    Anything else folds in. `main_servings=0` means we have no anchor, so rule 4
    keeps us silent rather than guessing.
    """
    verdict = BlockVerdict()
    for block in blocks[1:]:
        if _is_standalone(block, main_servings):
            verdict.standalone_names.append(block.name)
        else:
            verdict.component_names.append(block.name)
    return verdict


def _is_standalone(block: RecipeBlock, main_servings: int) -> bool:
    if _is_component_heading(block.name):
        return False
    if not block.ingredients:
        # An empty block is an extraction artefact, not a recipe — splitting it
        # off would produce a card with a name and nothing else.
        return False
    if main_servings <= 0 or block.servings <= 0:
        return False
    return block.servings != main_servings


# Ingredient headings that declare their OWN serving count, e.g.
#   "Składniki dla 4 osób:"      → 4
#   "**Składniki na 8 porcji**:" → 8
# Matched on the fetched page text, diacritic-folded first so "Składniki" and a
# mojibake-mangled "Skladniki" both hit. Markdown emphasis (**) and punctuation
# between the words are skipped rather than enumerated.
_SERVING_HEADING_RE = re.compile(
    r"skladnik\w*[\s*_:]*(?:dla|na)[\s*_]*(\d{1,2})[\s*_]*(?:os\w*|porcj\w*)",
)


def serving_headings(page_text: str) -> list[int]:
    """Serving counts declared by ingredient headings, in page order.

    A DETERMINISTIC cross-check on the extractor, added after live runs showed
    gpt-4o-mini reporting `components=[]` for a page whose two headings —
    "Składniki dla 4 osób" and "Składniki na 8 porcji" — are plainly in the
    fetched text. The extraction itself is fine; only the *reporting* of block
    structure was unreliable, and it was unreliable silently: an empty list is
    indistinguishable from a genuine single-recipe page, so the feature degraded
    back to the merged behaviour with nothing to notice it.

    This is the same rule the rest of the codebase already follows for
    `measures.py` and `pantry_math.py`: when the answer is mechanically derivable
    from text we already have, derive it — never spend a model call, and never
    depend on a model's consistency, for something a regex settles.

    Returns the counts only; deciding what they mean stays with `classify_blocks`.
    """
    return [int(m.group(1)) for m in _SERVING_HEADING_RE.finditer(fold_text(page_text))]


def page_declares_multiple_recipes(page_text: str) -> bool:
    """True when the page text shows two+ ingredient headings with DIFFERENT counts.

    Deliberately narrower than `classify_blocks`: this only answers "is the
    extractor's empty `components` contradicted by the page itself?". Distinct
    counts are required, so a single recipe whose heading is repeated (a summary
    box, a print view) does not trip it.
    """
    return len(set(serving_headings(page_text))) >= 2


def has_standalone_blocks(blocks: list[RecipeBlock], main_servings: int) -> bool:
    """True when the page carries a second real recipe worth asking about.

    The cheap gate on the common path: one recipe (or only components) returns
    False and the caller proceeds exactly as it does today.
    """
    return bool(classify_blocks(blocks, main_servings).standalone_names)
