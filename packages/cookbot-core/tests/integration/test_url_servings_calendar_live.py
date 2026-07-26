"""Live e2e for the one-shot "URL + servings + date" request.

The reported prompt does three things in a single message:

    "Dodaj przepis do kalendarza dla 4 osób na 26.07 z <url>"
     └ add to calendar    └ for 4 people   └ on 26.07  └ from this URL

so one turn must: fetch + extract that URL, record how many people the USER
wants vs how many the PAGE serves, and land a calendar entry on the right date
carrying the real ingredients (the shopping list is built from those later).

The page (chilitonka.com curry + naan) is a WordPress blog that converts to
~238k chars of markdown, of which the recipe used to start at ~68.5k — past the
fetch cap, so the extractor saw only boilerplate and returned nothing. The fix
is `recipe_web_fetch_tool`, which drops <script>/<style> bodies BEFORE the
markdown conversion (markdownify's `strip=` keeps their text), moving the
ingredient list to ~5.1k. See web_search.py.

Servings note: this page states "Składniki dla 4 osób", and the request is also
for 4 — so the correct behaviour is that scaling is a NO-OP. That is exactly the
distinction under test: the recipe's own serving count is tracked separately
from the user's target, rather than quantities being blindly multiplied.

STEP 45 changed what a one-shot request against THIS page does. The page hosts
two independent recipes (curry "dla 4 osób" + naan "na 8 porcji"), so the turn no
longer silently merges them and lands a calendar entry — it ASKS which the user
wants, and the answer arrives on the next turn. The tests below were rewritten
accordingly: the merged 21-ingredient result they used to assert is now reachable
only by answering "razem". Nothing about extraction changed (it is still verbatim
per Rule 5) — only what the ChatAgent does with a page carrying two dishes.

Flakiness: measured 8/9 green runs on gpt-4o-mini. The two hermetic tests
(truncation + URL pinning) are deterministic; the LLM-driven ones depend on a
live page and the model's tool choices, so an occasional failure is expected
rather than a regression — re-run before investigating. The split tests add one
more such dependency: the extractor must actually REPORT both blocks in
`components`. The deterministic half of that contract is unit-tested in
tests/test_recipe_blocks.py and tests/test_agents/test_chat_split.py.
"""
import asyncio
import datetime as _dt

from pydantic_ai.exceptions import ModelHTTPError

from cookbot.agents.chat import (
    CalendarAddEvent,
    ChatAgentDeps,
    FinalRecipeEvent,
    OnboardingState,
    build_chat_agent,
    resolve_recipe,
    stream_chat_response,
)
from cookbot.agents.web_search import build_web_fetch_agent, recipe_web_fetch_tool, web_fetch_prompt
from cookbot.models.recipe import RecipeSummary


async def _say(agent, deps, history, text: str, *, retries: int = 4) -> tuple[str, list]:
    """Run one chat turn the way the WS handler does. Returns (reply_text, events).

    Mirrors the helper in test_chat_e2e_live.py (the integration package has no
    __init__.py, so it can't be imported across modules). Retries on OpenAI 429,
    which is transient infra rather than a behaviour failure.
    """
    for attempt in range(retries):
        deps.reset_turn()
        try:
            reply = ""
            async with stream_chat_response(agent, deps, history, text) as tokens:
                async for tok in tokens:
                    reply += tok
            return reply, list(deps.events)
        except ModelHTTPError as e:
            if e.status_code == 429 and attempt < retries - 1:
                await asyncio.sleep(2**attempt * 5)
                continue
            raise
    raise AssertionError("unreachable")

_CURRY_URL = (
    "https://chilitonka.com/2013/09/07/"
    "prawdopodobnie-najlepsze-curry-na-swiecie-z-rownie-smacznym-chlebkiem-naan/"
)

# The page's own curry ingredient list ("Składniki dla 4 osób").
_PAGE_SERVINGS = 4


def _ingredients_text(recipe) -> str:
    return " | ".join(recipe.ingredients).lower()


async def test_chilitonka_page_survives_fetch_truncation() -> None:
    """The scraping bug itself, asserted without an LLM in the loop.

    Guards the regression directly: after noise-stripping, the ingredient list
    must land well inside the character cap. If a future change reintroduces raw
    CSS/JS into the markdown, the recipe gets pushed past the cap and this fails
    before any (slow, paid) extraction test does.
    """
    result = await recipe_web_fetch_tool().function(_CURRY_URL)
    content = result["content"]

    assert "Składniki" in content, "ingredient header truncated away from the fetch window"

    offset = content.find("Składniki")
    assert offset < 20_000, (
        f"ingredient list starts at char {offset} — script/style noise is back in "
        f"the markdown, pushing the recipe toward the truncation cap"
    )

    # The real quantities must be present, not just the header.
    for token in ("4 piersi z kurczaka", "250 ml", "1 cebula", "dla 4 os"):
        assert token in content, f"expected {token!r} in the fetched page content"


async def test_url_is_pinned_not_retyped_by_the_model() -> None:
    """A pinned URL wins over whatever the model passes as the tool argument.

    The second half of the scraping bug: the URL survives the ChatAgent, then the
    fetch SUB-agent retypes it into its own web_fetch call and mangles the slug
    (live: "-naan/" → "-na-nan/"), 404s, and reports "no recipe on this page".
    Hermetic — the corrupted argument here stands in for the model's.
    """
    corrupted = _CURRY_URL.replace("naan", "na-nan")
    result = await recipe_web_fetch_tool(pinned_url=_CURRY_URL).function(corrupted)

    assert "Składniki" in result["content"], (
        "pinned URL was ignored — the corrupted argument was fetched instead"
    )


async def test_extraction_reports_the_pages_own_servings(pl_config) -> None:
    """Verbatim extraction: the page serves 4, and the extractor must say so.

    `servings` is what the SHOPPING LIST later scales from, so an extractor that
    guesses here silently corrupts every downstream quantity.
    """
    # Pin the URL, exactly as production does: the model retypes long slugs into
    # the tool argument and corrupts them (".../chlebkiem-naan/" came back as
    # ".../chlebkiem-na-nan/" → 404 → null recipe). Pinning removes that failure
    # mode; `test_url_is_pinned_not_retyped_by_the_model` covers it directly.
    agent = build_web_fetch_agent(pl_config, pinned_url=_CURRY_URL)
    recipe = (await agent.run(web_fetch_prompt(_CURRY_URL))).output

    assert recipe is not None, (
        "extraction returned None — the recipe was truncated away or the page "
        "was misread as having no recipe"
    )
    assert recipe.servings == _PAGE_SERVINGS, (
        f"page states 'Składniki dla 4 osób'; extractor reported {recipe.servings}"
    )

    ingr = _ingredients_text(recipe)
    # Quantities must be copied verbatim, not rescaled during extraction.
    assert "4 piersi z kurczaka" in ingr, f"chicken breasts missing/altered: {recipe.ingredients}"
    assert "250 ml" in ingr, f"cream amount missing/altered: {recipe.ingredients}"
    assert "cebul" in ingr, f"onion (cebula) missing: {recipe.ingredients}"

    assert recipe.steps, "no preparation steps extracted"
    assert recipe.source_url and recipe.source_url.startswith("http")


async def test_extractor_reports_both_blocks_on_a_multi_recipe_page(pl_config) -> None:
    """STEP 45: the page has TWO recipes and the extractor must say so.

    This is the live half of the feature's contract — everything downstream
    (the question, the split, the shopping list) keys off `components` being
    populated here, and only a real extraction proves the model reports it.

    Note what is NOT asserted: that the extractor decided anything. It reports
    blocks verbatim (Rule 5); `models/recipe_blocks.py` classifies them, and that
    pure logic is exhaustively unit-tested in tests/test_recipe_blocks.py.
    """
    from cookbot.models.recipe_blocks import has_standalone_blocks  # noqa: PLC0415

    agent = build_web_fetch_agent(pl_config, pinned_url=_CURRY_URL)
    recipe = (await agent.run(web_fetch_prompt(_CURRY_URL))).output

    assert recipe is not None, "extraction returned None"
    assert len(recipe.components) >= 2, (
        "the page hosts a curry AND a naan bread, but the extractor reported "
        f"{len(recipe.components)} block(s): {[b.name for b in recipe.components]}"
    )

    names = " | ".join(b.name for b in recipe.components).lower()
    assert "naan" in names, f"the naan block was not reported: {names!r}"

    # The naan's own "na 8 porcji" is the signal the whole heuristic rests on.
    naan = next(b for b in recipe.components if "naan" in b.name.lower())
    assert naan.servings == 8, (
        f"naan states 'na 8 porcji'; extractor reported {naan.servings}"
    )

    # …and that is enough for the classifier to call it a separate dish.
    assert has_standalone_blocks(recipe.components, recipe.servings), (
        "curry (4) + naan (8) must classify as two standalone recipes"
    )

    # Reporting blocks must NOT hollow out the main extraction — `ingredients`
    # still carries the whole page, which is what "keep together" reproduces.
    assert "4 piersi z kurczaka" in _ingredients_text(recipe)


async def test_resolve_recipe_for_4_people_does_not_rescale(pl_config) -> None:
    """User wants 4, page serves 4 → quantities must be left ALONE.

    The distinction the user asked about: "for how many people this is" vs "for
    how many the recipe is". When they match, scaling is a no-op — inflating
    amounts here would double the shopping list.
    """
    selected = RecipeSummary(
        name="Curry z kurczaka",
        description="d",
        difficulty="Medium",
        total_time_minutes=40,
        key_ingredients=["kurczak", "curry"],
        source="web_search",
        source_url=_CURRY_URL,
    )

    found = await resolve_recipe(
        selected, "1", OnboardingState(servings=4),
        config=pl_config, site_filter="", allow_ai_generated=False,
    )

    assert found.source == "web_search", (
        f"a real recipe page must resolve as web_search, got {found.source!r}"
    )
    recipe = found.recipe
    assert recipe.servings == 4

    # The page already served 4, so the chicken count must be untouched.
    ingr = _ingredients_text(recipe)
    assert "4 piersi z kurczaka" in ingr, (
        f"quantities were rescaled despite target == page servings: {recipe.ingredients}"
    )
    for inflated in ("8 piersi", "16 piersi", "500 ml"):
        assert inflated not in ingr, (
            f"ingredients inflated ({inflated!r}) although no scaling was needed: "
            f"{recipe.ingredients}"
        )
    assert recipe.source_url and recipe.source_url.startswith("http")


async def test_multi_recipe_page_asks_before_committing(pl_config) -> None:
    """STEP 45, end to end: the one-shot request now ASKS instead of merging.

    "Dodaj przepis do kalendarza dla 4 osób na 26.07 z <url>"

    Before STEP 45 this landed ONE calendar entry carrying 21 ingredients — a
    4-person curry that also bought 8 portions of naan, with no indication why.
    That silent merge is what must not happen.

    Two outcomes are acceptable and the model legitimately picks between them:
    it parks the question for the next turn, or — since this message already
    implies intent — resolves the split immediately into two separate recipes.
    Asserting one exact choreography would make this test fail on correct
    behaviour, so it asserts the REQUIREMENT: the two dishes never arrive merged
    into a single 21-ingredient recipe.
    """
    agent = build_chat_agent(pl_config)
    deps = ChatAgentDeps(config=pl_config, allow_ai_generated=False)
    history: list = []

    reply, events = await _say(
        agent, deps, history,
        f"Dodaj przepis do kalendarza dla 4 osób na 26.07 z {_CURRY_URL}",
    )

    cards = [e for e in events if isinstance(e, FinalRecipeEvent)]
    asked = deps.pending_split is not None

    assert asked or cards, (
        "the page produced neither a question nor a recipe; "
        f"events: {[type(e).__name__ for e in events]} / reply: {reply!r}"
    )

    if asked:
        # Parked for the next turn: nothing may be committed yet.
        assert not cards, "no recipe card may be shown before the user chooses"
        assert not [e for e in events if isinstance(e, CalendarAddEvent)], (
            "nothing may land on the calendar before the user chooses"
        )
        assert "naan" in reply.lower(), f"the question does not name naan: {reply!r}"
    else:
        # Resolved in-turn: the dishes must be SEPARATE, never merged.
        assert len(cards) >= 2, (
            f"the page's two dishes were merged into {len(cards)} card(s) — "
            "the silent-merge bug STEP 45 exists to prevent"
        )
        curry = cards[0].recipe
        curry_text = _ingredients_text(curry)
        for leaked in ("mąk", "drożdż"):
            assert leaked not in curry_text, (
                f"naan ingredient {leaked!r} on the curry: {curry.ingredients}"
            )


async def test_answering_split_yields_a_curry_without_naan_ingredients(pl_config) -> None:
    """The acceptance criterion the whole step exists for.

    After "rozdziel", the curry keeps servings=4 and carries NO flour or yeast —
    so a 4-person curry no longer buys 8 portions of bread. The naan survives as
    its own recipe at its own 8 portions, with the same source_url (Rule 5).
    """
    agent = build_chat_agent(pl_config)
    deps = ChatAgentDeps(config=pl_config, allow_ai_generated=False)
    history: list = []

    await _say(agent, deps, history, f"Znajdź przepis dla 4 osób z {_CURRY_URL}")
    assert deps.pending_split is not None, "expected the split question first"

    reply, events = await _say(agent, deps, history, "Rozdziel je na osobne przepisy")

    cards = [e for e in events if isinstance(e, FinalRecipeEvent)]
    assert len(cards) == 2, (
        f"expected two recipe cards after splitting, got {len(cards)}; reply: {reply!r}"
    )

    curry = cards[0].recipe
    assert curry.servings == 4, f"curry should stay at 4 portions, got {curry.servings}"
    curry_text = _ingredients_text(curry)
    for leaked in ("mąk", "drożdż"):
        assert leaked not in curry_text, (
            f"naan ingredient {leaked!r} still on the 4-person curry: {curry.ingredients}"
        )

    naan = cards[1].recipe
    assert "naan" in naan.name.lower(), f"second card is not the naan: {naan.name!r}"
    assert naan.servings == 8, f"naan should keep its own 8 portions, got {naan.servings}"

    # Provenance survives the split for BOTH dishes (Rule 5).
    for card in cards:
        assert card.recipe.source_url and card.recipe.source_url.startswith("http"), (
            f"provenance lost on {card.recipe.name!r}"
        )

    assert deps.pending_split is None, "the question should be answered and cleared"


async def test_answering_together_then_adds_the_merged_recipe(pl_config) -> None:
    """Choosing "razem" reproduces the pre-STEP-45 behaviour exactly.

    This is the assertion the old one-shot test carried: one merged recipe on the
    calendar for 26 July, carrying real ingredients — the payload the shopping
    list is later built from. It is still reachable, just no longer silent.
    """
    agent = build_chat_agent(pl_config)
    deps = ChatAgentDeps(config=pl_config, allow_ai_generated=False)
    history: list = []

    await _say(
        agent, deps, history,
        f"Dodaj przepis do kalendarza dla 4 osób na 26.07 z {_CURRY_URL}",
    )

    # The model may park the question or resolve it in-turn (see
    # test_multi_recipe_page_asks_before_committing). "Razem" is only meaningful
    # while a question is pending; otherwise ask for the merge explicitly.
    if deps.pending_split is not None:
        reply, events = await _say(agent, deps, history, "Zostaw je razem jako jeden przepis")
        cards = [e for e in events if isinstance(e, FinalRecipeEvent)]
        assert len(cards) == 1, f"'razem' must give ONE card, got {len(cards)}"
        merged_text = _ingredients_text(cards[0].recipe)
        assert "kurczak" in merged_text and "mąk" in merged_text, (
            f"the merged recipe should carry BOTH dishes: {cards[0].recipe.ingredients}"
        )
    else:
        reply, events = await _say(agent, deps, history, "Dodaj oba przepisy na 26.07")

    add_events = [e for e in events if isinstance(e, CalendarAddEvent)]
    if not add_events:
        # The model may need one more nudge to complete the original request.
        _reply, events = await _say(agent, deps, history, "Dodaj go na 26.07")
        add_events = [e for e in events if isinstance(e, CalendarAddEvent)]
    assert add_events, (
        f"expected the merged recipe to reach the calendar; reply: {reply!r}"
    )
    entry = add_events[0].entry

    # Date: 26.07 of the current year (the year-default behaviour).
    assert entry.date.endswith("-07-26"), f"expected ...-07-26, got {entry.date!r}"
    assert entry.date.startswith(str(_dt.date.today().year)), (
        f"calendar date not in the current year: {entry.date!r}"
    )

    # The entry must carry the REAL recipe — the shopping list is built from it.
    assert entry.recipe is not None, "calendar entry has no recipe payload"
    assert entry.recipe.get("ingredients"), "calendar entry carries no ingredients"
    assert entry.recipe.get("source_url", "").startswith("http"), (
        f"provenance lost on the calendar entry: {entry.recipe.get('source_url')!r}"
    )

    # Servings bookkeeping: the user asked for 4 and the page serves 4.
    assert entry.recipe.get("servings") == 4, (
        f"entry should record 4 servings, got {entry.recipe.get('servings')!r}"
    )

    entry_ingr = " | ".join(entry.recipe["ingredients"]).lower()
    assert "kurczak" in entry_ingr, f"curry ingredients missing from entry: {entry_ingr[:300]}"

    # A recipe card should also have been shown for the pasted URL.
    recipe_events = [e for e in events if isinstance(e, FinalRecipeEvent)]
    if recipe_events:
        assert recipe_events[0].recipe.source_url


async def test_prompt_for_eight_people_scales_and_records_both_counts(pl_config) -> None:
    """The mirror of the no-op test: page serves 4, user asks for 8 (STEP 49).

    Where `test_answering_together_then_adds_the_merged_recipe` proves we DON'T
    inflate when the counts already match, this proves we DO scale when they
    differ — and, crucially, that the entry records both numbers so the UI can say
    "Porcje: 8 (przeliczone z 4)" instead of an unverifiable "Porcje: 8".

    The ingredient list on the entry is what the shopping list is built from, so
    the doubled amounts landing here is the whole point of the feature.

    STEP 45 inserts the split question ahead of all this, so the request is
    answered with "razem" first: scaling a MERGED recipe is the behaviour STEP 49
    pinned, and it must survive the new question unchanged.
    """
    agent = build_chat_agent(pl_config)
    deps = ChatAgentDeps(config=pl_config, allow_ai_generated=False)
    history: list = []

    reply, events = await _say(
        agent, deps, history,
        f"Dodaj przepis do kalendarza dla 8 osób na 26.07 z {_CURRY_URL}",
    )

    if deps.pending_split is not None:
        reply, events = await _say(
            agent, deps, history, "Zostaw je razem jako jeden przepis"
        )

    add_events = [e for e in events if isinstance(e, CalendarAddEvent)]
    if not add_events:
        reply, events = await _say(agent, deps, history, "Dodaj go na 26.07 dla 8 osób")
        add_events = [e for e in events if isinstance(e, CalendarAddEvent)]
    assert add_events, (
        "expected a CalendarAddEvent for the 8-person request; "
        f"events: {[type(e).__name__ for e in events]} / reply: {reply!r}"
    )
    entry = add_events[0].entry

    assert entry.date.endswith("-07-26"), f"expected ...-07-26, got {entry.date!r}"

    # Both counts recorded: what the user gets, and what the page stated.
    assert entry.servings == 8, (
        f"entry should record the user's 8 portions, got {entry.servings!r}"
    )
    assert entry.source_servings == _PAGE_SERVINGS, (
        f"entry should record the page's own {_PAGE_SERVINGS} servings as the "
        f"scaling anchor, got {entry.source_servings!r}"
    )

    # Provenance survives the doubling (Rule 5).
    assert entry.recipe is not None, "calendar entry has no recipe payload"
    assert entry.recipe.get("source_url", "").startswith("http"), (
        f"provenance lost on the calendar entry: {entry.recipe.get('source_url')!r}"
    )

    # The amounts actually doubled. The page says "4 piersi z kurczaka"; at 8
    # portions that must have grown, and must not still read as 4.
    entry_ingr = " | ".join(entry.ingredients).lower()
    assert "kurczak" in entry_ingr or "piersi" in entry_ingr, (
        f"chicken missing from the scaled entry: {entry_ingr[:300]}"
    )
    assert "4 piersi z kurczaka" not in entry_ingr, (
        "entry still carries the page's 4-serving quantities despite servings=8 — "
        f"the portion count would be describing unscaled amounts: {entry_ingr[:300]}"
    )
