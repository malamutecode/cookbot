"""ChatAgent behaviour for multi-recipe pages (STEP 45).

`tests/test_recipe_blocks.py` covers the pure component-vs-standalone verdict.
This module covers what the ChatAgent DOES with it — the part that touches deps,
events, and Firestore-persisted state:

    one recipe          → no question, one card, byte-identical to today
    a sauce block       → folded in, one card, no question
    two real recipes    → NO card yet; a pending question parked in deps
    "rozdziel"          → two cards, each with its own servings, same source_url
    "razem"             → one merged card, today's behaviour exactly

The pending question must survive a reconnect (Architecture Rule 3), so it lives
in `ChatState`, never in a module global — asserted here rather than left to
review, because a reconnect losing it would strand the user mid-question.

No LLM: the fetch sub-agent is stubbed, and the split tool is called directly.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from cookbot.agents.chat import (
    ChatAgentDeps,
    ChatState,
    FinalRecipeEvent,
    OnboardingState,
    build_chat_agent,
    dump_chat_state,
    restore_chat_state,
)
from cookbot.models.recipe import Recipe
from cookbot.models.recipe_blocks import RecipeBlock
from cookbot.models.tenant import TenantConfig

_CONFIG = TenantConfig(
    tenant_id="test",
    persona="You are a helpful chef",
    language="pl",
    recipe_source_url="",
    allowed_origins=[],
)

_URL = "https://chilitonka.com/curry-z-chlebkiem-naan/"

_CURRY_INGREDIENTS = ["4 piersi z kurczaka", "250 ml śmietanki", "1 cebula"]
_NAAN_INGREDIENTS = ["500 g mąki", "110 g masła", "7 g drożdży"]


def _get_tool(agent, name):
    """Return the raw async function registered under a tool name."""
    return agent._function_toolset.tools[name].function


def _events_of(deps, event_cls):
    return [ev for ev in deps.events if isinstance(ev, event_cls)]


def _ctx(deps):
    ctx = MagicMock()
    ctx.deps = deps
    ctx.usage = None
    return ctx


def _stub_fetch(recipe: Recipe | None):
    """Replacement for build_web_fetch_agent yielding a fixed extraction."""
    class _Stub:
        async def run(self, *_a, **_k):  # noqa: ANN202
            return MagicMock(output=recipe)

    return lambda _config, **_kw: _Stub()


def _main_block(servings: int, ingredients: list[str]) -> RecipeBlock:
    """The page's FIRST block — the main recipe, per the extractor's contract."""
    return RecipeBlock(
        name="Curry z kurczaka",
        servings=servings,
        ingredients=list(ingredients),
        steps=["Podsmaż cebulę.", "Dodaj kurczaka."],
    )


def _page(
    *,
    servings: int,
    ingredients: list[str],
    extra_blocks: list[RecipeBlock] | None = None,
) -> Recipe:
    """A Recipe as the extractor returns it — verbatim, blocks merely REPORTED.

    `components` carries EVERY block with the main recipe at [0] (that is the
    contract the extractor prompt states), and stays empty on a single-recipe
    page. `ingredients`/`steps` always hold the whole page regardless, which is
    what "keep together" reproduces.
    """
    extra = extra_blocks or []
    return Recipe(
        name="Curry z kurczaka",
        description="Najlepsze curry.",
        ingredients=ingredients + [i for b in extra for i in b.ingredients],
        steps=["Podsmaż cebulę.", "Dodaj kurczaka."] + [s for b in extra for s in b.steps],
        prep_time_minutes=15,
        cook_time_minutes=30,
        difficulty="Medium",
        servings=servings,
        tips=[],
        source_url=_URL,
        components=[_main_block(servings, ingredients), *extra] if extra else [],
    )


def _naan_block() -> RecipeBlock:
    return RecipeBlock(
        name="Chlebek naan",
        servings=8,
        ingredients=list(_NAAN_INGREDIENTS),
        steps=["Wyrób ciasto.", "Usmaż na patelni."],
    )


def _sauce_block() -> RecipeBlock:
    return RecipeBlock(
        name="Sos miętowy",
        servings=0,
        ingredients=["pęczek mięty", "200 g jogurtu"],
        steps=["Zmiksuj."],
    )


# ── The common path: nothing changes ──────────────────────────────────────────

async def test_single_recipe_page_shows_a_card_and_asks_nothing() -> None:
    """One recipe on the page → today's behaviour, untouched.

    The first acceptance criterion. A question here would fire on virtually every
    page in the product, so this is the test that protects the common path.
    """
    page = _page(servings=4, ingredients=_CURRY_INGREDIENTS)
    deps = ChatAgentDeps(config=_CONFIG)

    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(page)):
        agent = build_chat_agent(_CONFIG)
        found = await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL)

    assert found.source == "web_search"
    assert len(_events_of(deps, FinalRecipeEvent)) == 1
    assert deps.pending_split is None, "no question should be pending"
    assert not found.split_question, "the agent must not be told to ask"


async def test_component_block_folds_in_without_asking() -> None:
    """A sauce with no serving count of its own stays part of the dish.

    Second acceptance criterion: this already works today by merging, and the
    feature must not turn it into a question.
    """
    page = _page(
        servings=4,
        ingredients=_CURRY_INGREDIENTS,
        extra_blocks=[_sauce_block()],
    )
    deps = ChatAgentDeps(config=_CONFIG)

    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(page)):
        agent = build_chat_agent(_CONFIG)
        found = await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL)

    assert deps.pending_split is None
    assert not found.split_question
    assert len(_events_of(deps, FinalRecipeEvent)) == 1


# ── Two real recipes: ask ─────────────────────────────────────────────────────

async def test_two_standalone_recipes_ask_instead_of_showing_a_card() -> None:
    """The chilitonka page must ask, and must NOT commit to a card yet.

    Emitting a card and then asking would show the merged 21-ingredient recipe
    the step exists to prevent, so the question comes first and the card follows
    the user's answer.
    """
    page = _page(
        servings=4,
        ingredients=_CURRY_INGREDIENTS,
        extra_blocks=[_naan_block()],
    )
    deps = ChatAgentDeps(config=_CONFIG)

    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(page)):
        agent = build_chat_agent(_CONFIG)
        found = await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL)

    assert found.split_question, "the agent was not told to ask"
    assert found.split_options == ["Curry z kurczaka", "Chlebek naan"], (
        "the question must name both dishes so the user can answer meaningfully"
    )
    assert _events_of(deps, FinalRecipeEvent) == [], (
        "no recipe card may be sent before the user answers"
    )
    assert deps.pending_split is not None


async def test_pending_split_survives_a_reconnect() -> None:
    """Architecture Rule 3: the question is in ChatState, not a module global.

    A reconnect between the question and the answer lands on a fresh Cloud Run
    container. If the pending blocks did not round-trip, the user's "rozdziel"
    would arrive with nothing to split.
    """
    page = _page(
        servings=4, ingredients=_CURRY_INGREDIENTS, extra_blocks=[_naan_block()]
    )
    deps = ChatAgentDeps(config=_CONFIG)

    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(page)):
        agent = build_chat_agent(_CONFIG)
        await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL)

    snapshot = dump_chat_state(deps, [])
    assert ChatState.model_validate(snapshot).pending_split is not None

    revived = ChatAgentDeps(config=_CONFIG)
    restore_chat_state(snapshot, revived)

    assert revived.pending_split is not None
    assert [b.name for b in revived.pending_split.blocks] == [
        "Curry z kurczaka",
        "Chlebek naan",
    ]


# ── Answering the question ────────────────────────────────────────────────────

async def _ask_then(mode: str, deps: ChatAgentDeps | None = None):
    """Run the question turn, then answer it with `mode`. Returns (deps, result)."""
    page = _page(
        servings=4, ingredients=_CURRY_INGREDIENTS, extra_blocks=[_naan_block()]
    )
    deps = deps or ChatAgentDeps(config=_CONFIG, onboarding=OnboardingState(servings=4))

    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(page)):
        agent = build_chat_agent(_CONFIG)
        await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL)
        deps.reset_turn()
        result = await _get_tool(agent, "choose_recipe_split")(_ctx(deps), mode=mode)

    return deps, result


async def test_split_yields_two_independent_recipes() -> None:
    """The core acceptance criterion.

    Curry keeps servings=4 and must carry NO flour or yeast; naan becomes its own
    recipe at servings=8. Two cards, in page order.
    """
    deps, result = await _ask_then("split")

    cards = _events_of(deps, FinalRecipeEvent)
    assert len(cards) == 2, f"expected two recipe cards, got {len(cards)}"

    curry, naan = cards[0].recipe, cards[1].recipe

    assert curry.name == "Curry z kurczaka"
    assert curry.servings == 4
    curry_text = " | ".join(curry.ingredients).lower()
    for leaked in ("mąki", "drożdży", "masła"):
        assert leaked not in curry_text, (
            f"naan ingredient {leaked!r} leaked into the curry: {curry.ingredients}"
        )
    assert "4 piersi z kurczaka" in curry.ingredients

    assert naan.name == "Chlebek naan"
    assert naan.servings == 8, (
        f"naan must keep its own 8 portions, got {naan.servings}"
    )
    assert "500 g mąki" in naan.ingredients

    assert result.recipe_count == 2


async def test_split_preserves_source_url_on_every_recipe() -> None:
    """Rule 5: both dishes came from that page, so both keep the link."""
    deps, _ = await _ask_then("split")

    for event in _events_of(deps, FinalRecipeEvent):
        assert event.recipe.source_url == _URL, (
            f"provenance lost on {event.recipe.name!r}: {event.recipe.source_url!r}"
        )


async def test_keep_together_reproduces_the_merged_behaviour() -> None:
    """Choosing "razem" must give exactly what the product does today.

    One card, both ingredient lists merged, the page's own serving count — the
    behaviour the live integration test currently pins.
    """
    deps, result = await _ask_then("together")

    cards = _events_of(deps, FinalRecipeEvent)
    assert len(cards) == 1

    merged = cards[0].recipe
    text = " | ".join(merged.ingredients)
    for ingredient in _CURRY_INGREDIENTS + _NAAN_INGREDIENTS:
        assert ingredient in text, f"{ingredient!r} dropped from the merged recipe"
    assert merged.servings == 4
    assert merged.source_url == _URL
    assert result.recipe_count == 1


async def test_answering_clears_the_pending_question() -> None:
    """One question, asked once.

    A pending split left behind would make the next turn re-ask, or let a later
    unrelated "rozdziel" resurrect a stale page.
    """
    deps, _ = await _ask_then("split")

    assert deps.pending_split is None
    assert ChatState.model_validate(dump_chat_state(deps, [])).pending_split is None


async def test_split_sets_last_recipe_for_add_to_calendar() -> None:
    """`add_to_calendar` trusts deps.last_recipe (STEP 49), so it must be set.

    After a split the main dish is the one the user is most likely to add next;
    leaving last_recipe empty would make the entry fall back to onboarding and
    lose the ingredients.
    """
    deps, _ = await _ask_then("split")

    assert deps.last_recipe is not None
    assert deps.last_recipe.recipe.name == "Curry z kurczaka"
    assert deps.last_recipe.recipe.servings == 4


async def test_choosing_with_nothing_pending_is_contained() -> None:
    """Rule 7: a stray tool call must not crash the turn.

    The model can call this tool spontaneously ("rozdziel" with no page in play).
    It must return a structured nothing, not raise.
    """
    deps = ChatAgentDeps(config=_CONFIG)
    agent = build_chat_agent(_CONFIG)

    result = await _get_tool(agent, "choose_recipe_split")(_ctx(deps), mode="split")

    assert result.recipe_count == 0
    assert _events_of(deps, FinalRecipeEvent) == []


async def test_servings_survive_the_split_early_return() -> None:
    """A serving count given with a pasted link must not be lost to the question.

    `onboarding.servings` is the single anchor everything scales from (STEP 46),
    and `choose_recipe_split` reads it on a LATER turn. The split path returns
    early, so it has to persist the count on the way out. It didn't: observed
    live, "dodaj dla 8 osób z <url>" on the curry+naan page produced a calendar
    entry stamped 4 portions — the page's own count, describing a list the user
    never asked for.
    """
    page = _page(
        servings=4, ingredients=_CURRY_INGREDIENTS, extra_blocks=[_naan_block()]
    )
    deps = ChatAgentDeps(config=_CONFIG)          # onboarding deliberately EMPTY

    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(page)):
        agent = build_chat_agent(_CONFIG)
        found = await _get_tool(agent, "get_recipe_from_url")(
            _ctx(deps), url=_URL, servings=8,
        )

    assert found.split_question, "expected the split question for this page"
    assert deps.onboarding.servings == 8, (
        "the user's 8 portions were dropped by the early return — the later "
        "choose_recipe_split turn would scale to the wrong count"
    )


async def test_split_scales_the_main_dish_to_the_users_servings() -> None:
    """The other half: the persisted count is actually applied on the answer turn.

    Only the MAIN dish scales. The user asked for "curry for 8"; the naan keeps
    its own 8-portion count because nobody asked to resize the bread.
    """
    page = _page(
        servings=4, ingredients=_CURRY_INGREDIENTS, extra_blocks=[_naan_block()]
    )
    scaled = ["8 piersi z kurczaka", "500 ml śmietanki", "2 cebule"]

    class _ScaleStub:
        async def run(self, *_a, **_k):  # noqa: ANN202
            return MagicMock(output=MagicMock(ingredients=scaled))

    deps = ChatAgentDeps(config=_CONFIG)
    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(page)), \
         patch("cookbot.agents.chat.build_recipe_scale_agent", lambda _c: _ScaleStub()):
        agent = build_chat_agent(_CONFIG)
        await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL, servings=8)
        deps.reset_turn()
        await _get_tool(agent, "choose_recipe_split")(_ctx(deps), mode="split")

    cards = _events_of(deps, FinalRecipeEvent)
    assert cards[0].recipe.servings == 8, "main dish not scaled to the user's count"
    assert cards[0].recipe.ingredients == scaled
    assert cards[1].recipe.servings == 8, "naan keeps its OWN 8 portions, unscaled"
    assert cards[1].recipe.ingredients == _NAAN_INGREDIENTS


# ── The deterministic cross-check on the extractor ────────────────────────────

async def test_empty_components_are_retried_when_the_page_says_otherwise(
    monkeypatch,
) -> None:
    """The fix for the live flakiness that shipped broken once already.

    gpt-4o-mini returned `components=[]` for the curry+naan page on a meaningful
    fraction of live turns. An empty list is indistinguishable from a genuine
    single-recipe page, so the feature silently fell back to the merged
    21-ingredient behaviour with nothing to notice. When a regex over the fetched
    text finds serving headings the model didn't report, we re-ask once.
    """
    missed = _page(servings=4, ingredients=_CURRY_INGREDIENTS)   # components == []
    recovered = _page(
        servings=4, ingredients=_CURRY_INGREDIENTS, extra_blocks=[_naan_block()]
    )

    async def _page_text(_url: str, cache: dict[str, str] | None = None) -> str:
        return "Składniki dla 4 osób:\n- kurczak\n\nSkładniki na 8 porcji:\n- mąka"

    monkeypatch.setattr("cookbot.agents.chat.fetch_page_text", _page_text)

    calls: list[str] = []

    def _factory(_config, **_kw):  # noqa: ANN202
        class _Stub:
            async def run(self, prompt, *_a, **_k):  # noqa: ANN202
                calls.append(prompt)
                # First call = normal extraction; second = the split retry.
                return MagicMock(output=recovered if len(calls) > 1 else missed)

        return _Stub()

    deps = ChatAgentDeps(config=_CONFIG)
    with patch("cookbot.agents.chat.build_web_fetch_agent", _factory):
        agent = build_chat_agent(_CONFIG)
        found = await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL)

    assert len(calls) == 2, "the contradicted extraction was not retried"
    assert "8" in calls[1], "the retry must tell the model the counts it missed"
    assert found.split_question, "recovery should have produced the question"
    assert deps.pending_split is not None


async def test_no_retry_when_the_page_has_one_heading(monkeypatch) -> None:
    """The common path must cost nothing extra.

    A single-recipe page agrees with `components=[]`, so no second model call may
    fire — otherwise the feature taxes every ordinary recipe in the product.
    """
    page = _page(servings=4, ingredients=_CURRY_INGREDIENTS)

    async def _page_text(_url: str, cache: dict[str, str] | None = None) -> str:
        return "Składniki dla 4 osób:\n- kurczak"

    monkeypatch.setattr("cookbot.agents.chat.fetch_page_text", _page_text)

    calls: list[str] = []

    def _factory(_config, **_kw):  # noqa: ANN202
        class _Stub:
            async def run(self, prompt, *_a, **_k):  # noqa: ANN202
                calls.append(prompt)
                return MagicMock(output=page)

        return _Stub()

    deps = ChatAgentDeps(config=_CONFIG)
    with patch("cookbot.agents.chat.build_web_fetch_agent", _factory):
        agent = build_chat_agent(_CONFIG)
        found = await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL)

    assert len(calls) == 1, "a single-heading page must not trigger a retry"
    assert not found.split_question
    assert len(_events_of(deps, FinalRecipeEvent)) == 1


async def test_retry_that_still_reports_nothing_keeps_todays_behaviour(
    monkeypatch,
) -> None:
    """Fail safe: if the model won't report blocks even when pushed, ship the card.

    A pushed model is likelier to distort, and inventing a split from a list we
    cannot cleanly attribute would be worse than the merged card. So the original
    extraction wins and the user sees exactly what they see today.
    """
    missed = _page(servings=4, ingredients=_CURRY_INGREDIENTS)

    async def _page_text(_url: str, cache: dict[str, str] | None = None) -> str:
        return "Składniki dla 4 osób:\n- kurczak\n\nSkładniki na 8 porcji:\n- mąka"

    monkeypatch.setattr("cookbot.agents.chat.fetch_page_text", _page_text)

    deps = ChatAgentDeps(config=_CONFIG)
    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(missed)):
        agent = build_chat_agent(_CONFIG)
        found = await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL)

    assert not found.split_question
    assert deps.pending_split is None
    assert len(_events_of(deps, FinalRecipeEvent)) == 1, "the user must still get a card"


async def test_a_failed_page_fetch_never_breaks_the_turn() -> None:
    """Rule 7: the cross-check only ever ADDS confidence.

    A network hiccup while fetching the page text must degrade to "no
    cross-check" and still deliver the recipe — never take the turn down with it.
    `fetch_page_text` swallows the error itself, so this drives the REAL function
    with its underlying fetch tool made to raise — proving the catch is in the
    function, not faked by the stub, without touching the network.
    """
    from cookbot.agents.chat import fetch_page_text  # noqa: PLC0415

    async def _boom(*_a, **_k) -> str:
        raise RuntimeError("network down")

    with patch("cookbot.agents.chat.fetch_page_markdown", _boom):
        assert await fetch_page_text("https://x.test/p") == ""

    page = _page(servings=4, ingredients=_CURRY_INGREDIENTS)
    deps = ChatAgentDeps(config=_CONFIG)
    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(page)):
        agent = build_chat_agent(_CONFIG)
        found = await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL)

    assert found.source == "web_search", "the turn survived without a cross-check"
    assert len(_events_of(deps, FinalRecipeEvent)) == 1


# ── The answer-turn routing prompt ────────────────────────────────────────────

def test_pending_split_prompt_mandates_the_split_tool() -> None:
    """The fix for the live tool-routing failure.

    Answering "Rozdziel je na osobne przepisy", gpt-4o-mini called
    `propose_recipes` TWICE and web-searched for brand-new curry and naan
    recipes — discarding the page it had already extracted. Without a dynamic
    prompt the answer turn looks like any other turn, so the model reaches for
    its most familiar tool.
    """
    from cookbot.agents.chat import PendingSplit, onboarding_status_prompt  # noqa: PLC0415

    pending = PendingSplit(
        recipe=_page(servings=4, ingredients=_CURRY_INGREDIENTS, extra_blocks=[_naan_block()]),
        blocks=[_main_block(4, _CURRY_INGREDIENTS), _naan_block()],
        standalone_names=["Chlebek naan"],
    )

    prompt = onboarding_status_prompt(
        OnboardingState(), ["q"] * 5,
        last_proposals=[], last_recipe=None, pending_split=pending,
    )

    assert "choose_recipe_split" in prompt
    assert "Chlebek naan" in prompt, "the question must name the dishes"
    assert "propose_recipes" in prompt, "must explicitly forbid re-searching"
    assert "split" in prompt and "together" in prompt, "both modes must be spelled out"


def test_pending_split_prompt_wins_over_incomplete_onboarding() -> None:
    """Priority matters: a pasted link leaves onboarding almost empty.

    Guards a real off-by-one in the branch order — nesting the split branch under
    `ob.complete` would skip it on exactly the common path (a pasted link fills
    nothing but `servings`), sending the answer turn back into onboarding
    questions instead of resolving the split.
    """
    from cookbot.agents.chat import PendingSplit, onboarding_status_prompt  # noqa: PLC0415

    pending = PendingSplit(
        recipe=_page(servings=4, ingredients=_CURRY_INGREDIENTS, extra_blocks=[_naan_block()]),
        blocks=[_main_block(4, _CURRY_INGREDIENTS), _naan_block()],
        standalone_names=["Chlebek naan"],
    )

    prompt = onboarding_status_prompt(
        OnboardingState(servings=4),          # deliberately INCOMPLETE
        ["q"] * 5, last_proposals=[], last_recipe=None, pending_split=pending,
    )

    assert "choose_recipe_split" in prompt
    assert "ONBOARDING IN PROGRESS" not in prompt


def test_no_pending_split_leaves_the_prompt_untouched() -> None:
    """The common path must be byte-identical to before the feature."""
    from cookbot.agents.chat import onboarding_status_prompt  # noqa: PLC0415

    before = onboarding_status_prompt(
        OnboardingState(), ["q1", "q2", "q3", "q4", "q5"],
        last_proposals=[], last_recipe=None,
    )
    after = onboarding_status_prompt(
        OnboardingState(), ["q1", "q2", "q3", "q4", "q5"],
        last_proposals=[], last_recipe=None, pending_split=None,
    )

    assert before == after
    assert "choose_recipe_split" not in before


# ── The structural guard behind the prompt ────────────────────────────────────
#
# The prompt branch above is ADVICE, and live runs prove gpt-4o-mini overrides
# it: answering "Rozdziel je na osobne przepisy" it called propose_recipes and
# web-searched for fresh curry and naan recipes, discarding the extracted page
# and returning a menu of 3 curry + 4 naan variants instead of two cards. These
# tests pin the enforcement layer, which is what actually makes it impossible.


async def _deps_awaiting_split() -> ChatAgentDeps:
    """A deps with a real pending split, produced the way the product does."""
    page = _page(
        servings=4, ingredients=_CURRY_INGREDIENTS, extra_blocks=[_naan_block()]
    )
    deps = ChatAgentDeps(config=_CONFIG)
    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(page)):
        agent = build_chat_agent(_CONFIG)
        await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL)
    assert deps.pending_split is not None, "fixture failed to park a question"
    deps.reset_turn()
    return deps


async def test_propose_recipes_is_refused_while_a_split_is_pending() -> None:
    """The exact live failure: a fresh web search discarding the extracted page.

    Asserts no search ran at all (no proposals, no event) — being told "no" but
    still paying for a DuckDuckGo round-trip would leave the user's answer turn
    just as slow, and `last_proposals` overwritten.
    """
    from cookbot.agents.chat import RecipeOptionsEvent  # noqa: PLC0415

    deps = await _deps_awaiting_split()
    agent = build_chat_agent(_CONFIG)

    result = await _get_tool(agent, "propose_recipes")(
        _ctx(deps), dish_type="kurczak w kremowym sosie curry", ingredients=[],
    )

    assert result.count == 0
    assert _events_of(deps, RecipeOptionsEvent) == [], "no options may be shown"
    assert deps.last_proposals == [], "the search must not have run"
    assert "choose_recipe_split" in result.message, (
        "the refusal must name the tool that CAN make progress, or the model "
        f"just tries something else: {result.message!r}"
    )
    assert deps.pending_split is not None, "the question must survive the refusal"


async def test_get_recipe_details_is_refused_while_a_split_is_pending() -> None:
    """The second escape hatch: resolving a 'pick' with no proposals live.

    ModelRetry rather than a result, mirroring how this tool already handles an
    unmatched choice — it steers the model instead of fabricating a recipe.
    """
    from pydantic_ai import ModelRetry  # noqa: PLC0415

    deps = await _deps_awaiting_split()
    agent = build_chat_agent(_CONFIG)

    try:
        await _get_tool(agent, "get_recipe_details")(_ctx(deps), choice="1")
    except ModelRetry as exc:
        assert "choose_recipe_split" in str(exc)
    else:
        raise AssertionError("get_recipe_details resolved during a pending split")

    assert _events_of(deps, FinalRecipeEvent) == []
    assert deps.pending_split is not None


async def test_refetching_the_pending_page_is_refused() -> None:
    """Re-fetching the page we already hold is waste, and can drift.

    The extraction cost a model call and lives in `pending_split`; a second fetch
    could return different blocks, so the parked question would no longer match
    the page the user was asked about.
    """
    from pydantic_ai import ModelRetry  # noqa: PLC0415

    deps = await _deps_awaiting_split()
    before = deps.pending_split

    calls: list[str] = []

    def _factory(_config, **_kw):  # noqa: ANN202
        class _Stub:
            async def run(self, *_a, **_k):  # noqa: ANN202
                calls.append("fetched")
                return MagicMock(output=None)

        return _Stub()

    with patch("cookbot.agents.chat.build_web_fetch_agent", _factory):
        agent = build_chat_agent(_CONFIG)
        try:
            await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=_URL)
        except ModelRetry as exc:
            assert "choose_recipe_split" in str(exc)
        else:
            raise AssertionError("the pending page was re-fetched")

    assert calls == [], "no fetch may fire for the page already extracted"
    assert deps.pending_split is before, "the parked question must be untouched"


async def test_a_different_url_supersedes_the_pending_question() -> None:
    """The guard must not trap the user: a NEW link is a change of mind.

    Refusing every URL would strand someone who pasted the wrong link and simply
    pasted the right one. The new page proceeds normally and the stale question
    is dropped, because the answer would have nothing left to apply to.
    """
    other = "https://kwestiasmaku.com/inne-danie/"
    page = _page(servings=2, ingredients=["2 jajka"])
    page.source_url = other

    deps = await _deps_awaiting_split()

    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(page)):
        agent = build_chat_agent(_CONFIG)
        found = await _get_tool(agent, "get_recipe_from_url")(_ctx(deps), url=other)

    assert found.source == "web_search"
    assert len(_events_of(deps, FinalRecipeEvent)) == 1, "the new page must show"
    assert deps.pending_split is None, "the superseded question must be cleared"


async def test_the_answer_tool_still_works_behind_the_guard() -> None:
    """The guard blocks the search tools ONLY — the answer path stays open.

    Guards the obvious way to break this: a check placed somewhere shared would
    refuse `choose_recipe_split` too, deadlocking the conversation with a
    question nothing can answer.
    """
    deps = await _deps_awaiting_split()
    agent = build_chat_agent(_CONFIG)

    result = await _get_tool(agent, "choose_recipe_split")(_ctx(deps), mode="split")

    assert result.recipe_count == 2
    assert len(_events_of(deps, FinalRecipeEvent)) == 2
    assert deps.pending_split is None


def test_same_url_ignores_cosmetic_differences() -> None:
    """The model retypes URLs, so scheme/www/trailing slash/case must not matter.

    If they did, a re-fetch of the SAME page would read as a different one and
    slip past the guard — the exact hole the guard exists to close.
    """
    from cookbot.agents.chat import _same_url  # noqa: PLC0415

    assert _same_url(_URL, _URL)
    assert _same_url("https://chilitonka.com/a/", "http://www.chilitonka.com/a")
    assert _same_url("https://Chilitonka.com/A/", "https://chilitonka.com/A")

    assert not _same_url(_URL, "https://chilitonka.com/inne/")
    assert not _same_url(None, _URL), "an unknown source must never match"
    assert not _same_url("", ""), "two blanks are not the same page"
    assert not _same_url("https://x.test/p?id=1", "https://x.test/p?id=2"), (
        "a query can select a different recipe — do not strip it"
    )


# ── Proposal path parity ──────────────────────────────────────────────────────

async def test_proposal_path_asks_too() -> None:
    """A multi-recipe page reached by PICKING a proposal, not pasting a link.

    Same page, same problem — the question belongs to the page, not to how the
    user arrived at it, so `get_recipe_details` must behave like
    `get_recipe_from_url` here.
    """
    from cookbot.agents.chat import RecipeOptionsEvent  # noqa: PLC0415
    from cookbot.models.recipe import RecipeSummary  # noqa: PLC0415

    page = _page(
        servings=4, ingredients=_CURRY_INGREDIENTS, extra_blocks=[_naan_block()]
    )
    deps = ChatAgentDeps(
        config=_CONFIG,
        onboarding=OnboardingState(servings=4),
        last_proposals=[
            RecipeSummary(
                name="Curry z kurczaka",
                description="d",
                difficulty="Medium",
                total_time_minutes=45,
                key_ingredients=["kurczak"],
                source="web_search",
                source_url=_URL,
            )
        ],
    )

    with patch("cookbot.agents.chat.build_web_fetch_agent", _stub_fetch(page)):
        agent = build_chat_agent(_CONFIG)
        found = await _get_tool(agent, "get_recipe_details")(_ctx(deps), choice="1")

    assert found.split_question, "picking a proposal must ask the same question"
    assert _events_of(deps, FinalRecipeEvent) == []
    assert _events_of(deps, RecipeOptionsEvent) == []
    assert deps.pending_split is not None
