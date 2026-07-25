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

Flakiness: measured 8/9 green runs on gpt-4o-mini. The two hermetic tests
(truncation + URL pinning) are deterministic; the three LLM-driven ones depend on
a live page and the model's tool choices, so an occasional failure is expected
rather than a regression — re-run before investigating.
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


async def test_prompt_adds_scaled_recipe_to_calendar(pl_config) -> None:
    """The reported prompt, verbatim, in a single turn.

    "Dodaj przepis do kalendarza dla 4 osób na 26.07 z <url>"

    Must produce a calendar entry on 26 July of the current year, carrying the
    recipe extracted from THAT url with real ingredients attached — that payload
    is what the shopping list is later generated from.
    """
    agent = build_chat_agent(pl_config)
    deps = ChatAgentDeps(config=pl_config, allow_ai_generated=False)
    history: list = []

    reply, events = await _say(
        agent, deps, history,
        f"Dodaj przepis do kalendarza dla 4 osób na 26.07 z {_CURRY_URL}",
    )

    add_events = [e for e in events if isinstance(e, CalendarAddEvent)]
    assert add_events, (
        "expected a CalendarAddEvent from the one-shot request; "
        f"events: {[type(e).__name__ for e in events]} / reply: {reply!r}"
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
