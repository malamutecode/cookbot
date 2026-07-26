"""
Guided conversational chat agent.

Architecture
------------
One agent instance per WebSocket connection (built once, reused across turns).
Deps instance is also per-connection — onboarding state accumulates there across
turns.  Per-turn side-effects are appended to deps.events (ordered) by tools and
cleared each turn by reset_turn(); the WS handler drains them in order.

Onboarding flow
---------------
The agent collects 5 fields in order via update_onboarding tool calls, reading
the current state from deps.onboarding (which the WS handler keeps alive).
Once complete, it calls find_recipe.  If the first user message is already a
clear recipe request the agent may fill multiple fields at once and skip
remaining questions.

Sub-agents (stateless, one LLM call each)
------------------------------------------
  WebSearchAgent  — tries to find a real recipe on the web
  RecipeGenAgent  — generates a recipe when web search returns nothing
"""
from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.usage import RunUsage, UsageLimits

from cookbot.agents.recipe_gen import build_recipe_gen_agent, recipe_gen_prompt
from cookbot.agents.recipe_options import (
    build_recipe_options_agent,
    populate_proposal_images,
    recipe_options_prompt,
)
from cookbot.agents.recipe_scale import build_recipe_scale_agent, scale_recipe_to_servings
from cookbot.agents.recipe_search_fast import (
    build_fast_proposals,
    fast_path_query,
    is_fast_path_request,
)
from cookbot.agents.shopping_list import build_shopping_list_agent
from cookbot.agents.web_search import (
    build_web_fetch_agent,
    build_web_search_agent,
    fetch_page_markdown,
    web_fetch_prompt,
    web_fetch_prompt_split_retry,
    web_search_prompt,
)
from cookbot.models.calendar import CalendarEntry, CalendarState, MealSlot
from cookbot.models.pantry_math import PantryOutcome
from cookbot.models.pantry_math import subtract_pantry as subtract_pantry_from_list
from cookbot.models.recipe import ParsedIngredients, Recipe, RecipeSummary, UserIntent
from cookbot.models.recipe_blocks import (
    RecipeBlock,
    classify_blocks,
    page_declares_multiple_recipes,
    serving_headings,
)
from cookbot.models.shopping import ShoppingList
from cookbot.models.spizarnia import SpizarniaItem
from cookbot.models.tenant import TenantConfig

log = structlog.get_logger()

# dish_type values that mean "the user has NOT named a dish yet". The prompt asks
# for the literal "any", but the model paraphrases into the tenant's language
# ("jakiekolwiek", "cokolwiek", "obiad"), and a vague value here is what decides
# whether onboarding continues or a web search fires. Compared lowercased.
_VAGUE_DISH_SENTINELS = frozenset({
    "", "any", "anything", "whatever",
    # Polish paraphrases of "any / whatever / doesn't matter"
    "jakiekolwiek", "jakikolwiek", "jakakolwiek", "cokolwiek", "coś", "cos",
    "wszystko", "dowolne", "dowolny", "obojętnie", "obojetnie", "nie wiem",
    # Meal slots are a COURSE, not a dish — "obiad" alone is not searchable.
    "obiad", "kolacja", "śniadanie", "sniadanie", "lunch", "dinner", "breakfast",
    "supper", "przekąska", "przekaska", "deser", "dessert",
})


# ── Onboarding state ─────────────────────────────────────────────────────────

class OnboardingState(BaseModel):
    dish_type: str | None = None
    servings: int | None = None
    max_time_minutes: int | None = None
    ingredients: list[str] | None = None
    free_notes: str | None = None

    @property
    def complete(self) -> bool:
        return all(
            v is not None
            for v in [self.dish_type, self.servings, self.max_time_minutes,
                      self.ingredients, self.free_notes]
        )

    def next_missing_field(self) -> str | None:
        if self.dish_type is None:
            return "dish_type"
        if self.servings is None:
            return "servings"
        if self.max_time_minutes is None:
            return "max_time_minutes"
        if self.ingredients is None:
            return "ingredients"
        if self.free_notes is None:
            return "free_notes"
        return None

    def has_concrete_dish(self) -> bool:
        """True when the user named a specific dish (not the "any" sentinel used
        for "zaproponuj coś"). This is the signal for a direct recipe request.

        The sentinel set is not just {"any"}: the prompt asks the model to record
        a vague answer as dish_type="any", but it paraphrases in the tenant's
        language instead. Observed live on "Obiad" → dish_type="jakiekolwiek",
        which passed this gate, fired the fast path, and searched DuckDuckGo for
        "jakiekolwiek przepis" — returning basketball rules and a TikTok video as
        recipe cards. Treat every vague placeholder as "no dish named yet".
        """
        return bool(self.dish_type) and self.dish_type.strip().lower() not in _VAGUE_DISH_SENTINELS

    def ready_to_search(self) -> bool:
        """The agent may propose recipes when onboarding is complete OR when the
        user already named a concrete dish — the other fields have sensible
        defaults, so a specific request shouldn't be blocked on them."""
        return self.complete or self.has_concrete_dish()

    def to_intent(self) -> UserIntent:
        return UserIntent(
            dish_type=self.dish_type or "any",
            servings=self.servings or 2,
            max_time_minutes=self.max_time_minutes or 0,
            available_ingredients=self.ingredients or [],
            free_notes=self.free_notes or "",
        )


# ── Tool result models ────────────────────────────────────────────────────────

class FoundRecipe(BaseModel):
    recipe: Recipe
    source: str  # "web_search" | "ai_generated" | "not_found" | "error"
    # True when the user picked a WEB proposal but the page couldn't be read, so
    # the content was AI-generated instead. The chat agent tells the user.
    web_pick_fell_back: bool = False
    # Multi-recipe page (STEP 45): the page held a second STANDALONE dish, so no
    # card was sent — the agent must ask whether to split before committing.
    # `split_options` names the dishes so the question can be concrete.
    split_question: bool = False
    split_options: list[str] = []


class OnboardingUpdateResult(BaseModel):
    complete: bool
    next_missing_field: str | None
    collected: OnboardingState
    # True when the user has named a concrete dish → the agent should call
    # propose_recipes now instead of asking the remaining onboarding questions.
    ready_to_search: bool = False
    next_action: str = ""  # human-readable instruction for the model


class ProposeRecipesResult(BaseModel):
    count: int
    names: list[str]
    message: str


class PendingSplit(BaseModel):
    """A multi-recipe page waiting on the user's split/keep-together answer.

    Holds everything needed to produce EITHER outcome without re-fetching the
    page — the extraction already cost a model call, and re-running it on the
    answer turn would double the cost and could return something different.

    Lives in `ChatState` (Architecture Rule 3), never a module global: the
    question and the answer are two separate WS turns, and a reconnect between
    them lands on a fresh Cloud Run container. Losing this would strand the user
    holding a question nothing can answer.
    """

    recipe: Recipe                  # the merged extraction, verbatim ("together")
    blocks: list[RecipeBlock]       # every block, main at [0] ("split")
    standalone_names: list[str]     # which blocks the heuristic flagged as dishes


class ChooseSplitResult(BaseModel):
    mode: str                # "split" | "together"
    recipe_count: int        # cards emitted (0 when nothing was pending)
    recipe_names: list[str] = []
    message: str = ""


class CalendarAddResult(BaseModel):
    entry_id: str
    date: str
    recipe_name: str
    meal_slot: MealSlot = MealSlot.OBIAD
    # Portion bookkeeping (STEP 49) — surfaced to the ChatAgent so it can confirm
    # naturally ("Dodałem na 26.07, 8 porcji"). None = unknown; never invent one.
    servings: int | None = None
    source_servings: int | None = None


class CalendarRemoveResult(BaseModel):
    removed: bool
    entry_id: str


class ShoppingListResult(BaseModel):
    item_count: int
    sections: list[str]
    date_from: str
    date_to: str
    # Set when the ShoppingListAgent failed — tells the chat agent to apologise.
    error: str | None = None
    # Pantry subtraction (STEP 51), all empty/False unless the user turned it on.
    # The agent mentions these so a dropped or reduced line isn't a silent edit.
    pantry_subtracted: bool = False
    pantry_covered: list[str] = []   # items dropped — the pantry covers them fully
    pantry_reduced: list[str] = []   # items whose quantity was lowered
    pantry_flagged: list[str] = []   # kept at full quantity, tagged "check the pantry"


# ── Turn events (ordered side-effects, drained by the WS handler) ──────────────
# Tools append one of these to deps.events in the order they occur during a turn.
# The WS handler emits them in that order — it no longer hand-orders side-effects.

class FinalRecipeEvent(BaseModel):
    kind: Literal["final_recipe"] = "final_recipe"
    recipe: Recipe
    source: str  # "web_search" | "ai_generated"


class RecipeOptionsEvent(BaseModel):
    kind: Literal["recipe_options"] = "recipe_options"
    proposals: list[RecipeSummary]


class CalendarAddEvent(BaseModel):
    kind: Literal["calendar_add"] = "calendar_add"
    entry: CalendarEntry


class CalendarRemoveEvent(BaseModel):
    kind: Literal["calendar_remove"] = "calendar_remove"
    entry_id: str


class ShoppingListEvent(BaseModel):
    kind: Literal["shopping_list"] = "shopping_list"
    shopping_list: ShoppingList


TurnEvent = (
    FinalRecipeEvent
    | RecipeOptionsEvent
    | CalendarAddEvent
    | CalendarRemoveEvent
    | ShoppingListEvent
)


# ── Agent deps (one instance per WS connection) ───────────────────────────────

@dataclass
class ChatAgentDeps:
    """
    One instance per WebSocket connection. Fields fall into three lifetimes:

    1. Connection-durable — created once, survive every turn (do NOT reset).
       Persisted to Firestore between turns via dump_chat_state(), so a
       reconnect on a fresh container resumes the conversation.
    2. Per-turn input — refreshed by the WS handler at the start of each turn
       from the message payload / user's Firestore prefs.
    3. Per-turn output — `events`, an ordered list of side-effects appended by
       tools as they run, drained into WS messages by the handler after the turn,
       then cleared by reset_turn() before the next turn.

    The reset contract lives in reset_turn() (called by the WS handler), NOT as
    loose lines scattered in the handler — add a per-turn field here and to
    reset_turn() together so the two never drift.
    """

    # ── 1. Connection-durable ────────────────────────────────────────────────
    config: TenantConfig
    onboarding: OnboardingState = field(default_factory=OnboardingState)  # accumulates until complete
    last_recipe: FoundRecipe | None = None             # set by get_recipe_details, used by add_to_calendar
    last_proposals: list[RecipeSummary] = field(default_factory=list)  # proposals (1-4), consumed by get_recipe_details
    # Multi-recipe page awaiting the user's answer (STEP 45). Set by the fetch
    # tools, consumed by choose_recipe_split on a LATER turn — hence durable and
    # part of the Firestore snapshot, so a reconnect mid-question still resolves.
    pending_split: PendingSplit | None = None

    # ── 2. Per-turn input (refreshed each turn by the WS handler) ─────────────
    # Loaded from Firestore ONCE at the handshake (STEP 52), not re-sent per turn:
    # the server is the only writer on this path, so `_emit_event` keeps this same
    # object current in memory as it persists each add/remove. Read-only here —
    # the tools mutate the calendar by emitting events, never by editing this.
    calendar: CalendarState = field(default_factory=CalendarState)
    search_site_filter: str = ""                       # hard site: restriction (sites_only mode only)
    preferred_sites: list[str] = field(default_factory=list)  # soft-prefer domains (sites_and_internet mode)
    allow_ai_generated: bool = True                    # when False, skip RecipeGenAgent fallback
    # Pantry subtraction (STEP 51). `pantry` is the user's Firestore spiżarnia and
    # is READ-ONLY here; `subtract_pantry` is a per-turn flag from the message
    # payload (NOT a connect-time query param — it must be togglable mid-session).
    # Independent of the "use pantry ingredients" proposal hint, which never
    # reaches deps at all — it is a text suffix added by the WS handler.
    pantry: list[SpizarniaItem] = field(default_factory=list)
    subtract_pantry: bool = False

    # Raw text of the user's current message, set by stream_chat_response before
    # the run. Tools use it to recover EXACT literals the model may have retyped
    # inexactly — notably pasted URLs (see _url_from_user_message).
    current_user_message: str = ""

    # ── 3. Per-turn output (cleared by reset_turn) ────────────────────────────
    # Ordered side-effects, appended by tools in call order, drained by the WS handler.
    events: list[TurnEvent] = field(default_factory=list)
    # Total tokens (input + output) this turn spent, incl. sub-agent calls. Set by
    # stream_chat_response after the stream; the WS handler meters it against the
    # user's quota. 0 until a turn completes.
    last_turn_total_tokens: int = 0

    def reset_turn(self) -> None:
        """Clear the per-turn output events. Call once at the start of every WS
        turn, before streaming the agent response. Connection-durable and
        per-turn-input fields are intentionally left untouched."""
        self.events = []
        self.last_turn_total_tokens = 0


# ── Durable conversation state (Firestore-backed, Architecture Rule 3) ────────

class ChatState(BaseModel):
    """Snapshot of everything needed to resume a conversation on a fresh
    container: the PydanticAI message history plus the connection-durable
    deps fields. The WS handler saves it after every turn and restores it
    on (re)connect.

    The message history is stored as a JSON string rather than nested
    documents — Firestore rejects directly nested arrays, which tool-call
    parts can contain.
    """
    messages_json: str = "[]"
    onboarding: OnboardingState = OnboardingState()
    last_recipe: FoundRecipe | None = None
    last_proposals: list[RecipeSummary] = []
    pending_split: PendingSplit | None = None


def dump_chat_state(
    deps: ChatAgentDeps, message_history: list[ModelMessage]
) -> dict[str, Any]:
    """Serialize the resumable conversation state to a Firestore-safe dict."""
    state = ChatState(
        messages_json=ModelMessagesTypeAdapter.dump_json(message_history).decode(),
        onboarding=deps.onboarding,
        last_recipe=deps.last_recipe,
        last_proposals=deps.last_proposals,
        pending_split=deps.pending_split,
    )
    return state.model_dump(mode="json")


def restore_chat_state(
    raw: dict[str, Any], deps: ChatAgentDeps
) -> list[ModelMessage]:
    """Restore connection-durable deps fields from a dump_chat_state() dict
    and return the deserialized message history."""
    state = ChatState.model_validate(raw)
    deps.onboarding = state.onboarding
    deps.last_recipe = state.last_recipe
    deps.last_proposals = state.last_proposals
    deps.pending_split = state.pending_split
    return list(ModelMessagesTypeAdapter.validate_json(state.messages_json))


# ── URL recovery ──────────────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+")


def _url_from_user_message(model_url: str, user_message: str) -> str:
    """Prefer the URL as the USER actually typed it over the model's retyped one.

    A tool's `url` argument is generated text: the LLM re-types the link token by
    token and can corrupt long slugs. Observed live on the chilitonka curry post,
    where "...chlebkiem-naan/" came back as "...chlebkiem-naaan/" — a 404, so the
    extraction failed and the user was told the page had no recipe.

    When the user's message contains exactly one URL we trust that literal. With
    several links we pick the one closest to what the model produced (longest
    common prefix) so multi-link messages still resolve sensibly, and fall back
    to the model's string when the message has no URL at all (e.g. the link came
    from an earlier turn).
    """
    candidates = _URL_RE.findall(user_message or "")
    if not candidates:
        return model_url
    if len(candidates) == 1:
        return candidates[0]

    def _shared_prefix(candidate: str) -> int:
        n = 0
        for a, b in zip(candidate, model_url):
            if a != b:
                break
            n += 1
        return n

    return max(candidates, key=_shared_prefix)


# ── Date normalisation ────────────────────────────────────────────────────────

def _same_dish(resolved_name: str, entry_name: str) -> bool:
    """Do the resolved recipe and the calendar entry refer to the same dish?

    Guards the STEP 49 rule that the resolved recipe overrides the model's
    ingredient argument: that override is only correct when both names describe
    the same thing. Adding a SECOND dish in the same turn ("dodaj też sałatkę")
    must not inherit the previous recipe's ingredients and portion count.

    Deliberately lenient — the model paraphrases its own tool arguments ("Curry z
    kurczaka" vs "curry"), so exact equality would disable the override in the
    common case. Compared case-insensitively with one name containing the other.
    """
    a = (resolved_name or "").strip().casefold()
    b = (entry_name or "").strip().casefold()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _normalize_date(raw: str) -> str:
    """Coerce a date string into strict YYYY-MM-DD.

    The frontend calendar matches day cells by exact ISO string, so a value like
    "2026-06-4", "4.06.2026", "04/06/2026" or "4.06" (year-less) would silently
    fail to render. We accept the common forms the LLM emits and zero-pad; a
    year-less date assumes the current year. If we can't parse it, return the
    input unchanged (better to pass it through than to crash the turn).

    Past-year guard: the LLM frequently emits a stale year (e.g. 2023) from its
    training prior even when told today's date. A meal plan is never in the past,
    so any year BEFORE the current year is bumped to the current year.
    """
    import re  # noqa: PLC0415
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415

    s = (raw or "").strip()
    try:
        this_year = datetime.now(ZoneInfo("Europe/Warsaw")).year
    except Exception:
        this_year = datetime.now(timezone(timedelta(hours=2))).year

    def _fix_year(y: int) -> int:
        return this_year if y < this_year else y

    # ISO-ish: YYYY-M-D or YYYY/M/D
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return f"{_fix_year(y):04d}-{mo:02d}-{d:02d}"

    # Day-first: D.M.YYYY or D/M/YYYY
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", s)
    if m:
        d, mo, y = (int(g) for g in m.groups())
        return f"{_fix_year(y):04d}-{mo:02d}-{d:02d}"

    # Year-less day-first: D.M or D/M → assume current year
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})", s)
    if m:
        d, mo = (int(g) for g in m.groups())
        return f"{this_year:04d}-{mo:02d}-{d:02d}"

    return s


# ── Recipe resolution (extracted from the get_recipe_details tool) ─────────────

def _select_proposal(
    proposals: list[RecipeSummary], choice: str
) -> RecipeSummary | None:
    """Map the user's choice ('2' or a name) to one of the shown proposals.

    Returns None when nothing matches — never guesses: silently resolving the
    wrong card is worse than asking the user to clarify (the tool raises
    ModelRetry in that case so the agent asks for a clear pick).
    """
    if not proposals:
        return None
    choice_stripped = choice.strip()
    if choice_stripped.isdigit():
        idx = int(choice_stripped) - 1
        if 0 <= idx < len(proposals):
            return proposals[idx]
    # Name matching, strictest first, and BEFORE any digit scan — a name may
    # legitimately contain a number ("fast-4", "Chili nr 2"), so treating a
    # digit anywhere in the string as a card index would pick the wrong card.
    # Substring matching also used to run in proposal order and return the FIRST
    # overlap, so with similarly-named cards ("Kotlet schabowy" / "Kotlet
    # schabowy tradycyjny") a pick of #2 silently resolved to #1. Anything
    # ambiguous now returns None so the caller asks.
    lower = choice_stripped.lower()
    exact = [p for p in proposals if p.name.lower() == lower]
    if len(exact) == 1:
        return exact[0]
    contained = [
        p for p in proposals
        if lower in p.name.lower() or p.name.lower() in lower
    ]
    if len(contained) == 1:
        return contained[0]
    # No name matched. A bare number may still arrive wrapped in the user's
    # phrasing ("wybieram 2", "poproszę nr 3") because `choice` is a
    # MODEL-GENERATED argument. Accept it only when the text contains exactly
    # ONE in-range number — two numbers means we'd be guessing which is the pick.
    numbers = [int(n) for n in re.findall(r"\d+", choice_stripped)]
    in_range = [n for n in numbers if 1 <= n <= len(proposals)]
    if len(in_range) == 1:
        return proposals[in_range[0] - 1]
    return None


def _cached_agent(
    cache: dict[str, Any] | None,
    key: str,
    factory: Callable[[TenantConfig], Any],
    config: TenantConfig,
) -> Any:
    """Build a stateless sub-agent via its module-level factory, reusing a
    previous build when a cache dict is provided. Lazy on purpose: an agent is
    only built when its branch actually runs, and the factory name is resolved
    at call time so tests can patch cookbot.agents.chat.build_*_agent."""
    if cache is None:
        return factory(config)
    if key not in cache:
        cache[key] = factory(config)
    return cache[key]


async def resolve_recipe(
    selected: RecipeSummary | None,
    choice: str,
    ob: OnboardingState,
    *,
    config: TenantConfig,
    site_filter: str,
    allow_ai_generated: bool,
    usage: RunUsage | None = None,
    agent_cache: dict[str, Any] | None = None,
) -> FoundRecipe:
    """Resolve a chosen proposal to a full Recipe.

    Decision tree (unchanged from the original get_recipe_details body):
      1. web_search proposal with a known URL → fetch that URL directly.
      2. web_search proposal without a URL → search by name (retry without the
         site filter if the filtered search finds nothing).
      3. Nothing found yet and AI allowed → generate with RecipeGenAgent.
      4. Nothing found and AI disabled → a "not_found" placeholder Recipe.

    `usage` is the parent run's RunUsage (pass ctx.usage from the tool) so
    sub-agent tokens aggregate into the chat turn's usage and limits.
    """
    recipe: Recipe | None = None

    if selected and selected.source == "web_search":
        servings = ob.servings or 2

        if selected.source_url:
            # The user picked a SPECIFIC page → that URL is the source of truth.
            # Extraction is occasionally flaky, so retry once before giving up.
            # Do NOT fall back to a name-search here: returning a recipe from a
            # different site would mis-attribute it (wrong recipe under a wrong
            # "source" link). If both attempts fail, fall through to AI generation.
            # Pin the proposal's URL into the fetch tool: the model retyping a long
            # slug into the tool argument corrupts it and 404s. Per-URL, so not cached.
            fetch_agent = build_web_fetch_agent(config, pinned_url=selected.source_url)
            for attempt in (1, 2):
                log.info("get_recipe_details_fetch_known_url",
                         url=selected.source_url, attempt=attempt)
                recipe = (await fetch_agent.run(
                    web_fetch_prompt(selected.source_url),
                    usage=usage,
                )).output
                if recipe is not None:
                    break
                log.info("get_recipe_details_fetch_attempt_empty",
                         url=selected.source_url, attempt=attempt)
        else:
            # The proposal had no URL → search the web by name (this is the only
            # way to reach a real page for this pick).
            ws_intent = UserIntent(
                dish_type=selected.name,
                servings=servings,
                max_time_minutes=0,
                available_ingredients=[],
                free_notes="",
            )
            ws_parsed = ParsedIngredients(items=[], must_use=[], dietary_hints=[], missing_staples=[])
            ws_agent = _cached_agent(agent_cache, "web_search", build_web_search_agent, config)
            recipe = (await ws_agent.run(
                web_search_prompt(ws_parsed, ws_intent, site_filter),
                usage=usage,
            )).output
            if recipe is None and site_filter:
                log.info("get_recipe_details_retry_no_filter", recipe_name=selected.name)
                recipe = (await ws_agent.run(
                    web_search_prompt(ws_parsed, ws_intent, site_filter=""),
                    usage=usage,
                )).output
        # Provenance safety net: a web recipe must carry a source_url. If the
        # extractor omitted it but the proposal knew the URL, backfill it so the
        # frontend always shows a "Źródło" link for web recipes.
        if recipe is not None and not recipe.source_url and selected.source_url:
            recipe.source_url = selected.source_url

        # Scaling is a SEPARATE step from the (verbatim) extraction above. Only now,
        # if the user's requested servings differ from what the page states, do we
        # rescale quantities. No-ops when the page's serving count matches or is
        # unknown — provenance (name/steps/source_url) is preserved by the scaler.
        if recipe is not None:
            scale_agent = _cached_agent(agent_cache, "recipe_scale", build_recipe_scale_agent, config)
            before = recipe.servings
            recipe = await scale_recipe_to_servings(
                recipe, servings, agent=scale_agent, usage=usage,
            )
            if recipe.servings != before:
                log.info("get_recipe_details_scaled",
                         recipe_name=selected.name,
                         original_servings=recipe.original_servings,
                         target_servings=recipe.servings)

        log.info("get_recipe_details_result",
            recipe_name=selected.name,
            found=recipe is not None,
            source_url=recipe.source_url if recipe else None,
        )

    # For gen fallback use the full original onboarding context
    if selected:
        intent = UserIntent(
            dish_type=selected.name,
            servings=ob.servings or 2,
            max_time_minutes=selected.total_time_minutes,
            available_ingredients=ob.ingredients or selected.key_ingredients,
            free_notes=ob.free_notes or "",
        )
    else:
        intent = UserIntent(
            dish_type=choice,
            servings=ob.servings or 2,
            max_time_minutes=ob.max_time_minutes or 0,
            available_ingredients=ob.ingredients or [],
            free_notes=ob.free_notes or "",
        )
    parsed = ParsedIngredients(items=intent.available_ingredients, must_use=[], dietary_hints=[], missing_staples=[])

    # Did the user pick a web proposal that we then failed to read from the web?
    web_pick_fell_back = bool(selected and selected.source == "web_search" and recipe is None)

    if recipe is None and allow_ai_generated:
        gen_agent = _cached_agent(agent_cache, "recipe_gen", build_recipe_gen_agent, config)
        generated: Recipe = (await gen_agent.run(recipe_gen_prompt(parsed, intent), usage=usage)).output
        recipe = generated
        source = "ai_generated"
    elif recipe is not None:
        source = "web_search"
    else:
        # Web search returned nothing and AI generation is disabled —
        # surface a minimal placeholder so the agent can inform the user.
        recipe = Recipe(
            name=intent.dish_type,
            description="Nie znaleziono przepisu na tej stronie. Spróbuj zmienić ustawienia wyszukiwania.",
            ingredients=[],
            steps=[],
            prep_time_minutes=0,
            cook_time_minutes=0,
            difficulty="Easy",
            servings=ob.servings or 2,
            tips=[],
        )
        source = "not_found"

    return FoundRecipe(recipe=recipe, source=source, web_pick_fell_back=web_pick_fell_back)


# ── Multi-recipe pages (STEP 45) ──────────────────────────────────────────────

def detect_split(recipe: Recipe, page_text: str = "") -> PendingSplit | None:
    """Does this extraction hold a SECOND standalone dish worth asking about?

    Returns None on the common path — a page with one ingredient list leaves
    `components` empty, so this is a list check and nothing more: no extra model
    call, no measurable cost on the overwhelmingly common case.

    The judgement itself is the pure heuristic in `models/recipe_blocks.py`; this
    only adapts it to the extractor's output. `components[0]` is the main block
    by contract with the prompt, but a model can still return a single block or a
    ragged list, so anything that doesn't clearly describe two dishes falls
    through to None and behaves exactly as it does today.

    `page_text` (the fetched markdown, when the caller has it) is a DETERMINISTIC
    cross-check on that contract. Live runs showed gpt-4o-mini reporting
    `components=[]` for the curry+naan page on roughly a third of turns even
    though both headings are plainly in the text — and an empty list is
    indistinguishable from a real single-recipe page, so the feature silently
    degraded to the old merged behaviour. When the page's own headings contradict
    the model, we trust the page and log it (`split_detect_model_missed_blocks`).
    """
    blocks = recipe.components
    if len(blocks) < 2:
        if page_text and page_declares_multiple_recipes(page_text):
            log.info(
                "split_detect_model_missed_blocks",
                source_url=recipe.source_url,
                headings=serving_headings(page_text),
                reported_blocks=len(blocks),
            )
        return None

    # Anchor on the Recipe's own serving count rather than blocks[0].servings:
    # `servings` went through the same verbatim extraction the rest of the card
    # is built from, so comparing against it keeps the question consistent with
    # what the user will actually see.
    verdict = classify_blocks(blocks, recipe.servings)
    if not verdict.standalone_names:
        return None

    return PendingSplit(
        recipe=recipe,
        blocks=blocks,
        standalone_names=verdict.standalone_names,
    )


async def detect_split_verified(
    recipe: Recipe,
    *,
    config: TenantConfig,
    usage: RunUsage | None = None,
) -> tuple[PendingSplit | None, Recipe]:
    """`detect_split`, with a deterministic second opinion when the model says "no".

    Returns `(pending_or_None, recipe)` — the recipe too, because a successful
    re-extraction replaces the one passed in.

    Why this exists: `components` is the ONE part of the feature that depends on
    model consistency, and live runs proved it inconsistent — the curry+naan page
    came back with `components=[]` on a meaningful fraction of turns, silently
    restoring the merged 21-ingredient behaviour. The page text is unambiguous
    ("Składniki dla 4 osób" / "Składniki na 8 porcji"), so when a regex over the
    fetched markdown contradicts an empty `components`, we re-ask ONCE with those
    counts stated. The extra fetch is text-only; the extra model call happens only
    on pages that demonstrably have multiple serving headings — never on the
    common single-recipe path.

    Fails safe in every direction: no source_url, a failed fetch, a page with one
    heading, or a retry that still reports nothing all return the original recipe
    and no question, i.e. exactly today's behaviour.
    """
    pending = detect_split(recipe)
    if pending is not None or not recipe.source_url:
        return pending, recipe

    page_text = await fetch_page_text(recipe.source_url)
    if not page_text or not page_declares_multiple_recipes(page_text):
        return None, recipe

    headings = serving_headings(page_text)
    log.info("split_retry_extraction", source_url=recipe.source_url, headings=headings)
    try:
        retry_agent = build_web_fetch_agent(config, pinned_url=recipe.source_url)
        retried = (await retry_agent.run(
            web_fetch_prompt_split_retry(recipe.source_url, headings),
            usage=usage,
        )).output
    except Exception as exc:  # noqa: BLE001 — Rule 7: contain it, keep the recipe
        log.info("split_retry_failed", source_url=recipe.source_url, error=str(exc))
        return None, recipe

    if retried is None:
        return None, recipe
    if not retried.source_url:
        retried.source_url = recipe.source_url

    pending = detect_split(retried)
    if pending is None:
        # The model still won't report blocks. Keep the ORIGINAL extraction (the
        # retry prompt pushes hard and a pushed model is likelier to distort) and
        # stay silent — the merged card is wrong-ish, but inventing a split from a
        # list we can't cleanly attribute would be worse.
        log.info("split_retry_still_empty", source_url=recipe.source_url)
        return None, recipe

    log.info("split_retry_recovered", source_url=recipe.source_url,
             standalone=pending.standalone_names)
    return pending, retried


async def fetch_page_text(url: str) -> str:
    """Fetch a page's markdown with NO model call, for the deterministic checks.

    Reuses `recipe_web_fetch_tool` so the text is byte-identical to what the
    extractor saw (same noise-stripping, same cap) — a different fetch path would
    make the cross-check compare against a different document.

    Never raises: this only ever *adds* confidence, so a network hiccup must
    degrade to "no cross-check", not to a failed turn (Rule 7).
    """
    try:
        return await fetch_page_markdown(url)
    except Exception as exc:  # noqa: BLE001 — best-effort by design
        log.info("fetch_page_text_failed", url=url, error=str(exc))
        return ""


def _block_to_recipe(block: RecipeBlock, page: Recipe, *, is_main: bool) -> Recipe:
    """Turn one page block into a standalone Recipe.

    Provenance is not negotiable (Rule 5): every dish produced from the page keeps
    its `source_url` and `image_url`, because both really did come from there.

    Serving counts stay VERBATIM per block — the curry's 4 and the naan's 8 are
    each what their own block stated. Scaling to the user's target is a separate
    step that runs after this, exactly as it does for a single-recipe page.
    """
    return Recipe(
        name=block.name or page.name,
        # Only the main dish inherits the page's description/tips/times — they
        # describe the post as a whole, and stamping them onto the side dish
        # would attribute the curry's cooking time to the bread.
        description=page.description if is_main else "",
        ingredients=list(block.ingredients),
        steps=list(block.steps),
        prep_time_minutes=page.prep_time_minutes if is_main else 0,
        cook_time_minutes=page.cook_time_minutes if is_main else 0,
        difficulty=page.difficulty,
        servings=block.servings or page.servings,
        tips=list(page.tips) if is_main else [],
        source_url=page.source_url,
        image_url=page.image_url,
        original_servings=page.original_servings if is_main else None,
    )


def split_into_recipes(pending: PendingSplit) -> list[Recipe]:
    """The "rozdziel" outcome: one Recipe per block, in page order.

    Components are NOT dropped — a sauce block folds back into the main dish, so
    splitting a curry+naan+sauce page yields two recipes (curry incl. its sauce,
    and naan), never three. Only the blocks the heuristic flagged as standalone
    become separate cards.
    """
    page = pending.recipe
    standalone = set(pending.standalone_names)

    main_block = pending.blocks[0]
    main_ingredients = list(main_block.ingredients)
    main_steps = list(main_block.steps)
    others: list[Recipe] = []

    for block in pending.blocks[1:]:
        if block.name in standalone:
            others.append(_block_to_recipe(block, page, is_main=False))
        else:
            # A component belongs to the main dish; keep it there.
            main_ingredients.extend(block.ingredients)
            main_steps.extend(block.steps)

    merged_main = main_block.model_copy(
        update={"ingredients": main_ingredients, "steps": main_steps}
    )
    return [_block_to_recipe(merged_main, page, is_main=True), *others]


def merged_recipe(pending: PendingSplit) -> Recipe:
    """The "razem" outcome: today's behaviour, unchanged.

    The extraction already merged everything — that IS the current product
    behaviour — so keeping them together is simply the verbatim Recipe, minus the
    now-answered block reporting.
    """
    return pending.recipe.model_copy(update={"components": []})


# ── Structured proposal pick (no LLM in the selection path) ──────────────────

async def pick_proposal(
    deps: ChatAgentDeps,
    index: int,
    *,
    usage: RunUsage | None = None,
) -> FoundRecipe | None:
    """Resolve the proposal at 1-based `index` and update deps, with NO model call.

    A click on card N is already an unambiguous selection. Routing it through the
    ChatAgent meant an LLM had to turn "wybieram 2" back into choice="2", and a
    wrong guess there silently delivered another recipe — and then stamped it on
    the calendar, since add_to_calendar trusts `deps.last_recipe`.

    Mirrors get_recipe_details' post-processing (split detection, last_recipe,
    proposal clearing, FinalRecipeEvent) so both entry points leave identical
    state. Returns None when the index isn't a live proposal, letting the caller
    fall back to the conversational path rather than guessing.
    """
    if not deps.last_proposals or not 1 <= index <= len(deps.last_proposals):
        log.info("pick_proposal_out_of_range",
                 index=index, available=len(deps.last_proposals))
        return None
    selected = deps.last_proposals[index - 1]

    try:
        found = await resolve_recipe(
            selected,
            selected.name,
            deps.onboarding,
            config=deps.config,
            site_filter=deps.search_site_filter,
            allow_ai_generated=deps.allow_ai_generated,
            usage=usage,
        )
    except Exception as exc:
        # Leave proposals/last_recipe untouched so the user can simply retry.
        log.exception("pick_proposal_failed", index=index, error=str(exc))
        return FoundRecipe(
            recipe=Recipe(
                name=selected.name,
                description="",
                ingredients=[],
                steps=[],
                prep_time_minutes=0,
                cook_time_minutes=0,
                difficulty="Easy",
                servings=deps.onboarding.servings or 2,
                tips=[],
            ),
            source="error",
        )

    # Multi-recipe page (STEP 45) — the question belongs to the PAGE, so a
    # structured pick must ask exactly as the tool path does. No card and no
    # last_recipe yet; both wait for the answer.
    pending, resolved_recipe = await detect_split_verified(
        found.recipe, config=deps.config, usage=usage,
    )
    if pending is not None:
        deps.pending_split = pending
        deps.last_proposals = []
        log.info("pick_proposal_split_question",
                 index=index, standalone=pending.standalone_names)
        return FoundRecipe(
            recipe=resolved_recipe,
            source=found.source,
            web_pick_fell_back=found.web_pick_fell_back,
            split_question=True,
            split_options=[b.name for b in pending.blocks],
        )

    deps.last_recipe = found                     # durable — used by add_to_calendar
    deps.last_proposals = []                     # clear so selection prompt doesn't repeat
    if found.source in ("web_search", "ai_generated"):
        deps.events.append(FinalRecipeEvent(recipe=found.recipe, source=found.source))
    log.info("pick_proposal_resolved", index=index, name=found.recipe.name)
    return found


# ── Dynamic onboarding system prompt (module-level so it's unit-testable) ─────

def onboarding_status_prompt(
    ob: OnboardingState,
    questions: list[str],
    *,
    last_proposals: list[RecipeSummary],
    last_recipe: FoundRecipe | None,
    pending_split: PendingSplit | None = None,
) -> str:
    """Build the per-turn onboarding/routing system prompt.

    Four branches, in priority order: an unanswered split question (STEP 45),
    recipe-selection (proposals shown, awaiting pick), the direct recipe fast
    path (user named a concrete dish → search now), and guided onboarding (vague
    request → ask the next missing field)."""
    # ── Awaiting a split answer (STEP 45) ────────────────────────────────────
    # FIRST, before the ob.complete check: a pending split routinely coexists with
    # INCOMPLETE onboarding (a pasted link fills almost nothing), so nesting this
    # under ob.complete would silently skip it on exactly the common path.
    #
    # Without this branch the answer turn gets a generic prompt, and gpt-4o-mini
    # falls back to its most familiar tool: observed live answering "Rozdziel je
    # na osobne przepisy" with TWO propose_recipes calls that web-searched for
    # brand-new curry and naan recipes, discarding the page it already had.
    if pending_split is not None:
        names = "\n".join(f"  - {b.name}" for b in pending_split.blocks)
        return f"""## AWAITING A SPLIT ANSWER — do not call any other tool

You asked the user whether this page's recipes should be split. The page holds:
{names}

The user's message is their ANSWER. Call `choose_recipe_split` NOW, this turn:
  - "rozdziel" / "osobno" / "oddzielnie" / "tak" / naming one dish → mode="split"
  - "razem" / "jeden" / "wszystko razem" / "nie" → mode="together"

The recipes are ALREADY EXTRACTED and waiting — do NOT call propose_recipes,
get_recipe_details, or get_recipe_from_url, and do NOT search the web for these
dishes again. If the reply is genuinely unclear, ask once more; never guess.
"""

    # ── Awaiting a recipe pick ───────────────────────────────────────────────
    # BEFORE the ob.complete check, for the same reason as the split branch
    # above: proposals routinely coexist with INCOMPLETE onboarding, because
    # `has_concrete_dish()` lets "znajdź przepis na schabowego" search straight
    # away and leaves servings/time/ingredients/free_notes unset. Nested under
    # ob.complete this branch never fired on that path — the pick turn got the
    # generic onboarding prompt, the model passed the dish NAME instead of the
    # number, and substring matching resolved it to card #1 regardless of which
    # card the user actually clicked.
    if last_proposals and last_recipe is None:
        names = "\n".join(f"  {i+1}. {p.name}" for i, p in enumerate(last_proposals))
        return f"""## RECIPE SELECTION IN PROGRESS
The user has been shown these recipe options:
{names}

If the user's message picks one of them, call get_recipe_details immediately.
Pass the NUMBER of the chosen option as `choice` (e.g. "2" for the second one) —
a message like "wybieram 2" means choice="2". Only pass a name when the user
named a recipe without any number. Do not describe the chosen recipe — the full
recipe card is displayed automatically after the tool call. If the message is
instead a question or comment about the options, answer it directly and invite
them to pick one by number or name.
"""

    if ob.complete:
        return ""  # onboarding done, no extra instructions needed

    # ── Direct recipe request fast path ──────────────────────────────────────
    # If the user already named a concrete dish (not "zaproponuj coś"), they know
    # what they want — skip the remaining onboarding questions and search straight
    # away. The other fields keep their sensible defaults.
    if ob.has_concrete_dish():
        known = []
        if ob.dish_type:
            known.append(f"dish = {ob.dish_type!r}")
        if ob.servings is not None:
            known.append(f"servings = {ob.servings}")
        if ob.max_time_minutes is not None:
            known.append(f"time limit (min) = {ob.max_time_minutes}")
        if ob.ingredients is not None:
            known.append(f"ingredients = {ob.ingredients!r}")
        known_str = "\n".join(f"  {k}" for k in known) or "  (dish only)"
        return f"""
## DIRECT RECIPE REQUEST — the user named a specific dish

Already known from the conversation:
{known_str}

The user knows what they want — DO NOT ask the onboarding questions (servings,
time, ingredients, extra notes).

Call propose_recipes IMMEDIATELY — one single tool call, this turn. Pass every
detail the message gives, as arguments to that call: dish_type (required), plus
servings / max_time_minutes / ingredients / free_notes when the user stated them.
propose_recipes records them itself, so do NOT call update_onboarding first —
that extra round-trip only makes the user wait longer.
Do NOT ask any further questions before calling it.
"""

    # ── Guided onboarding (vague request) ────────────────────────────────────
    field_map: list[tuple[str, Any, str, str]] = [
        ("dish_type",        ob.dish_type,        "dish type",   questions[0]),
        ("servings",         ob.servings,          "servings",    questions[1]),
        ("max_time_minutes", ob.max_time_minutes,  "time",        questions[2]),
        ("ingredients",      ob.ingredients,       "ingredients", questions[3]),
        ("free_notes",       ob.free_notes,        "notes",       questions[4]),
    ]

    collected_lines = [
        f"  {label} = {value!r}"
        for _, value, label, _ in field_map
        if value is not None
    ]
    missing_lines = [
        f"  {i+1}. field={field!r}  →  ask: \"{question}\""
        for i, (field, value, _, question) in enumerate(field_map)
        if value is None
    ]

    next_field, _next_question = next(
        (field, question)
        for field, value, _, question in field_map
        if value is None
    )

    collected_str = "\n".join(collected_lines) or "  (none yet)"
    missing_str = "\n".join(missing_lines)

    return f"""
## ONBOARDING IN PROGRESS — follow these instructions exactly

Collected so far:
{collected_str}

Still needed (in order):
{missing_str}

MANDATORY STEPS FOR THIS TURN:
1. The user's message is a response to the question about {next_field!r}.
   Call update_onboarding immediately with the parsed value for {next_field!r}.
   Parsing rules:
   - Any dish name → dish_type = that name (e.g. "makaron" → "pasta" or keep as-is)
   - "zaproponuj" / "nie wiem" / "cokolwiek" / "surprise me" → dish_type = "any"
   - A number / "tylko dla mnie" / "dla dwojga" → servings as integer
   - Time like "30 minut" → max_time_minutes = 30; "bez pośpiechu" / "nie ma znaczenia" → 0
   - List of ingredients → ingredients = [list]; "nie" / "brak" / "nic" → ingredients = []
   - Any extra notes → free_notes = that text; "nie" / "nic" → free_notes = ""
2. After update_onboarding returns, FOLLOW its `next_action` field:
   - if `ready_to_search` is true (the user named a specific dish, or all fields are
     set) → call propose_recipes immediately; do NOT ask more questions.
   - otherwise → ask ONLY the next missing question.
3. NEVER re-ask a question whose field is already listed in "Collected so far".
"""


# ── Agent factory (call once per connection) ──────────────────────────────────

def build_chat_agent(config: TenantConfig) -> Agent[ChatAgentDeps, str]:  # noqa: C901
    questions = config.ui.intake_questions

    agent: Agent[ChatAgentDeps, str] = Agent(
        config.model_chat,
        output_type=str,
        deps_type=ChatAgentDeps,
        defer_model_check=True,
        instructions=f"""You are {config.persona} — a conversational cooking assistant.
You MUST respond exclusively in {config.language}. Never use another language.

## Capabilities
- Propose recipe options → call propose_recipes (sends 4 cards to the user).
- Get full recipe after user picks one → call get_recipe_details.
- Add a meal to the calendar → call add_to_calendar. There is NO button for this in
  the app — the calendar is filled by the user asking you in the chat, so whenever it
  is relevant, say so plainly and show them how to phrase it. Each calendar day has
  four meal sections; pass meal_slot when the user names one — "śniadanie" →
  sniadanie, "lunch" → lunch, "obiad" → obiad, "kolacja" → kolacja. If they do not
  say, omit it (it defaults to obiad); never ask just to fill this in.
  When the tool result has a `servings` value, mention it in your confirmation
  ("Dodałem na 26.07 — 8 porcji"), and if `source_servings` differs, say the
  amounts were converted from it. Never state a portion count the result did not
  give you; if `servings` is null, simply don't mention portions.
- Answer a "one recipe or two?" question about a page → call choose_recipe_split.
- Remove a meal from the calendar → call remove_from_calendar.
- Build a shopping list for a date range → call get_shopping_list.
  When `pantry_subtracted` is true, the user asked for their pantry to be deducted,
  so say what changed rather than letting it look like a mistake: name the items in
  `pantry_covered` as dropped because they already have them, mention that
  `pantry_reduced` amounts were lowered, and say `pantry_flagged` items are marked
  to check at home because the pantry entry gave no amount. Keep it to one short
  sentence; never list items these fields did not give you.
- Answer general cooking questions directly.

## Recipe flow
0a. If the user PASTES A RECIPE LINK (a URL) and wants that recipe or to add it,
   call get_recipe_from_url with the link — do NOT call propose_recipes or run
   onboarding. If the same message says how many people it is for ("dla 8 osób"),
   pass that number as the `servings` argument in the SAME call — it is the only
   place the count is recorded for a pasted link, and the quantities are rescaled
   to it. The recipe card is shown automatically; then offer to add it to the
   calendar the same explicit way as in step 4 — say that you can add it if they
   tell you the day and meal, with an example ("napisz np. 'dodaj na piątek na
   obiad'"). If it returns source="not_found" or "error", no card is shown —
   explain briefly (the page had no readable recipe / a temporary problem).
0b. If the user's message already names a SPECIFIC dish (e.g. "przepis na halloumi
   dla 2 osób"), they know what they want — do NOT ask the onboarding questions.
   Call propose_recipes straight away in a SINGLE tool call, passing every detail
   the message gives as arguments (here dish_type="halloumi", servings=2).
   propose_recipes records those itself, so do not call update_onboarding first.
   Passing the serving count matters — it is what the recipe is later scaled to.
   Only ask the guided questions when the request is vague ("coś na obiad",
   "zaproponuj coś").
1. When the user wants a recipe, call propose_recipes — this shows 4 options.
2. Tell the user to pick one (e.g. "Który przepis Cię interesuje?").
3. When the user picks (says a number or name), call get_recipe_details with their choice.
4. The full recipe card is sent to the user automatically — do NOT describe or summarise it.
   Do not retell the recipe, but DO make the next step obvious: the user cannot see
   any button for it, so spell out that adding to the calendar happens by asking
   here in the chat, and give a concrete example they can copy. Two short sentences,
   e.g. "Oto przepis! Jeśli chcesz, mogę dodać go do kalendarza — napisz kiedy,
   np. 'dodaj na sobotę na obiad' albo 'dodaj na 28.07 na kolację'."
   Mention the day and the meal section (śniadanie / lunch / obiad / kolacja) as the
   two things you need, and that they can also just say "dodaj na jutro" and you will
   use obiad. You may also offer to look for recipes for other days.
   - EXCEPTION: if get_recipe_details returns web_pick_fell_back=true, the chosen
     web page could not be read, so this recipe was AI-generated. Tell the user
     briefly and honestly, e.g. "Nie udało mi się odczytać tej strony, więc
     przygotowałem przepis samodzielnie." then offer the usual next steps.
   - If get_recipe_details returns source="not_found", no recipe was found on the
     allowed sites — no card is shown. Explain this and suggest changing the source
     settings or allowing AI-generated recipes.
   - If a tool reports source="error" or an error message, a temporary technical
     problem occurred — no card is shown. Apologise briefly and ask the user to
     try again in a moment. Do not retry the tool yourself.
5. MULTI-RECIPE PAGE: if get_recipe_from_url or get_recipe_details returns
   split_question=true, that page holds TWO SEPARATE DISHES and NO card was shown
   yet. Do not describe either recipe and do not call any other tool. Ask one
   short question naming both dishes from `split_options` and offering the two
   choices, e.g. "Ta strona zawiera dwa przepisy: Curry z kurczaka i Chlebek naan.
   Chcesz oba osobno, czy jako jeden przepis razem?".
   On the user's NEXT message call choose_recipe_split once: "osobno" / "rozdziel"
   / "tylko curry" / naming one dish → mode="split"; "razem" / "jeden" / "wszystko"
   → mode="together". If the reply is unclear, ask again rather than guessing.
   After it returns, the cards are shown automatically — do not retell them; just
   invite the next step (adding to the calendar) as in step 4.

## After the first recipe — free-chat mode
Once a recipe has been delivered, stay in free-chat mode indefinitely:
- User can ask for another recipe for a different day → call propose_recipes again
  (no need to re-run onboarding; reuse the same preferences or ask only what changed).
- User can ask to add the current recipe to another date → call add_to_calendar.
  If several turns pass after a recipe was shown and it still has not been added,
  it is fine to remind them once that you can put it in the calendar on request.
- User can change how many people the dish is for ("a jednak dla 6", "zrób to na 4
  osoby") → call update_onboarding with the new `servings`, then re-resolve the SAME
  recipe (get_recipe_details with the same choice, or get_recipe_from_url with the
  same link and the new `servings`) so the quantities are actually recalculated.
  Updating onboarding alone does not rescale a card that is already on screen.
  Keep the same dish — this is a rescale, not a new search.
- User can ask about shopping, substitutions, cooking tips → answer directly.
- If the user says "nowa rozmowa", "od nowa", "reset" or similar → acknowledge and
  tell them to use the restart button, or simply forget the context and start fresh
  by treating their next message as a brand-new request.

## Response style
- Be concise, warm, and practical.
- After propose_recipes, briefly invite the user to pick ("Który Cię interesuje?").
- After get_recipe_details, at most two short sentences — the card is shown
  automatically, so use them to invite the next step, not to repeat the recipe.
- After adding to calendar, confirm the date AND the meal section you used ("Dodałem
  na 28.07 na obiad"), so the user can see where it landed and correct it. Then
  suggest next steps — another day, or a shopping list for a date range.
- After a shopping list, say how many items there are.
- Never expose tool names or internal field names to the user.""",
    )

    # Inject current date on every turn so the model always has the correct value.
    @agent.system_prompt
    async def _current_date(_ctx: RunContext[ChatAgentDeps]) -> str:
        from datetime import datetime, timedelta, timezone  # noqa: PLC0415
        try:
            now = datetime.now(ZoneInfo("Europe/Warsaw")).date()
        except Exception:
            # tzdata not installed — fall back to UTC+2 (CET+1/CEST offset)
            now = datetime.now(timezone(timedelta(hours=2))).date()
        today = now.isoformat()
        year = now.year
        return (
            f"## Calendar\n"
            f"Today is {today} (current year: {year}).\n"
            f"All calendar dates MUST use this year unless the user states a different year explicitly.\n"
            f"- 'today' / 'dzisiaj' → {today}.\n"
            f"- A date without a year (e.g. '06.08', '8 sierpnia', '12th') → use year {year}, "
            f"giving YYYY-MM-DD. Never use a past year like 2023 or 2024.\n"
            f"- If that date has already passed this year, still use {year} unless the user says otherwise.\n"
            f"- Weeks start on MONDAY (Polish convention). For a shopping list over a relative "
            f"range, resolve it yourself into date_from/date_to: 'ten tydzień' / 'this week' → "
            f"the Monday..Sunday containing {today}; 'przyszły tydzień' / 'next week' → the "
            f"following Monday..Sunday; 'najbliższe X dni' → {today} plus X-1 days. Never ask "
            f"the user for exact dates when the range can be resolved this way."
        )

    # Dynamic system prompt — injected on every turn, shows exactly what's
    # been collected and what the model MUST do next. Delegates to the module-level
    # onboarding_status_prompt so the logic is unit-testable without a live run.
    @agent.system_prompt
    async def _onboarding_status(ctx: RunContext[ChatAgentDeps]) -> str:
        return onboarding_status_prompt(
            ctx.deps.onboarding,
            questions,
            last_proposals=ctx.deps.last_proposals,
            last_recipe=ctx.deps.last_recipe,
            pending_split=ctx.deps.pending_split,
        )

    # ── Tools ────────────────────────────────────────────────────────────────

    # Sub-agents are stateless and depend only on tenant config — build each
    # once per chat agent (i.e. per connection) and reuse across turns. Filled
    # lazily by _cached_agent so unused sub-agents are never built.
    sub_agents: dict[str, Any] = {}

    @agent.tool
    async def update_onboarding(
        ctx: RunContext[ChatAgentDeps],
        dish_type: str | None = None,
        servings: int | None = None,
        max_time_minutes: int | None = None,
        ingredients: list[str] | None = None,
        free_notes: str | None = None,
    ) -> OnboardingUpdateResult:
        """Record one or more onboarding answers. Always call this before asking the
        next question. After it returns, FOLLOW its `next_action`: if
        `ready_to_search` is true, call propose_recipes immediately (do not ask more
        questions); otherwise ask only the next missing question."""
        ob = ctx.deps.onboarding
        if dish_type is not None:
            ob.dish_type = dish_type
        if servings is not None:
            ob.servings = servings
        if max_time_minutes is not None:
            ob.max_time_minutes = max_time_minutes
        if ingredients is not None:
            ob.ingredients = ingredients
        if free_notes is not None:
            ob.free_notes = free_notes

        ready = ob.ready_to_search()
        if ob.complete:
            action = "All onboarding fields are set — call propose_recipes now."
        elif ob.has_concrete_dish():
            # Direct request: the user named a specific dish, so search now with the
            # collected values + defaults. Do NOT ask the remaining questions.
            action = (
                "The user named a specific dish — call propose_recipes NOW with the "
                "collected dish and any known servings/time/ingredients (defaults for "
                "the rest). Do NOT ask about time, ingredients, or extra notes."
            )
        else:
            nxt = ob.next_missing_field()
            action = f"Ask only the next onboarding question (field {nxt!r})."
        return OnboardingUpdateResult(
            complete=ob.complete,
            next_missing_field=ob.next_missing_field(),
            collected=ob,
            ready_to_search=ready,
            next_action=action,
        )

    @agent.tool
    async def propose_recipes(
        ctx: RunContext[ChatAgentDeps],
        dish_type: str,
        ingredients: list[str],
        max_time_minutes: int = 0,
        servings: int = 2,
        dietary_hints: list[str] | None = None,
        free_notes: str = "",
    ) -> ProposeRecipesResult:
        """Propose recipe options (4-6 cards). Call after onboarding is complete or on
        explicit user request. The options are sent to the frontend automatically."""
        cfg: TenantConfig = ctx.deps.config
        ob = ctx.deps.onboarding
        intent = UserIntent(
            dish_type=dish_type or ob.dish_type or "any",
            servings=servings or ob.servings or 2,
            max_time_minutes=max_time_minutes or ob.max_time_minutes or 0,
            available_ingredients=ingredients or ob.ingredients or [],
            free_notes=free_notes or ob.free_notes or "",
        )

        # Persist what this call was told, so a DIRECT request ("przepis na
        # halloumi dla 4 osób") records its context even though the model went
        # straight here without calling update_onboarding first.
        #
        # This is not just bookkeeping for the next turn: `servings` is what
        # resolve_recipe / get_recipe_from_url scale the chosen recipe to, and
        # they read it ONLY from deps.onboarding. Without this write the arguments
        # above are used for the search and then discarded, so picking a proposal
        # scaled to the `or 2` default instead of the user's stated count — right
        # by luck for "dla 2 osób", silently wrong for any other number.
        # Only fill blanks; never overwrite an answer the user already gave.
        if dish_type and not ob.dish_type:
            ob.dish_type = dish_type
        if servings and not ob.servings:
            ob.servings = servings
        if max_time_minutes and not ob.max_time_minutes:
            ob.max_time_minutes = max_time_minutes
        if ingredients and not ob.ingredients:
            ob.ingredients = ingredients
        if free_notes and not ob.free_notes:
            ob.free_notes = free_notes
        parsed = ParsedIngredients(
            items=intent.available_ingredients,
            must_use=[],
            dietary_hints=dietary_hints or [],
            missing_staples=[],
        )
        try:
            proposals: list[RecipeSummary] = []
            # ── Fast path (STEP 47) ──────────────────────────────────────────
            # A plain "przepis na X" has nothing for a model to reason about, so
            # skip the RecipeOptionsAgent entirely: DDG + deterministic ranking
            # produce the cards with zero LLM calls (~2s vs ~8-15s). Images and
            # metadata come from each page's <head> in the same fetch.
            if is_fast_path_request(ob, ctx.deps.current_user_message):
                started = time.monotonic()
                fast = await build_fast_proposals(
                    fast_path_query(ob, site_filter=ctx.deps.search_site_filter),
                    limit=cfg.proposal_count_fast,
                )
                elapsed_ms = int((time.monotonic() - started) * 1000)
                # Too few good pages ⇒ fall through; the user gets a slower but
                # normal result rather than a thin set of cards.
                hit = len(fast) >= cfg.proposal_min_fast
                log.info("propose_recipes_fast_path", hit=hit, count=len(fast),
                         elapsed_ms=elapsed_ms, dish=ob.dish_type)
                if hit:
                    proposals = fast

            if not proposals:
                opts_agent = _cached_agent(sub_agents, "recipe_options", build_recipe_options_agent, cfg)
                result = await opts_agent.run(
                    recipe_options_prompt(
                        parsed, intent,
                        site_filter=ctx.deps.search_site_filter,
                        allow_ai_generated=ctx.deps.allow_ai_generated,
                        preferred_sites=ctx.deps.preferred_sites,
                    ),
                    usage=ctx.usage,
                )
                proposals = result.output.proposals[:cfg.proposal_count]
                # Best-effort: fill dish images from each web page's og:image (concurrent,
                # never blocks the result — failures leave image_url=None for a placeholder).
                await populate_proposal_images(proposals)
        except Exception as exc:
            # Contain the failure at the tool boundary: the turn survives and the
            # agent explains instead of the connection dying mid-stream.
            log.exception("propose_recipes_failed", error=str(exc))
            return ProposeRecipesResult(
                count=0,
                names=[],
                message="error: recipe search failed temporarily — apologise briefly "
                        "and invite the user to try again in a moment. Do not retry now.",
            )
        ctx.deps.last_proposals = proposals          # durable — consumed by get_recipe_details
        ctx.deps.events.append(RecipeOptionsEvent(proposals=proposals))
        return ProposeRecipesResult(
            count=len(proposals),
            names=[p.name for p in proposals],
            message="Options sent to user — ask them to pick one by number or name.",
        )

    @agent.tool
    async def get_recipe_details(
        ctx: RunContext[ChatAgentDeps],
        choice: str,
    ) -> FoundRecipe:
        """Get the full recipe for the option the user chose. choice is a number (1-6) or the recipe name."""
        selected = _select_proposal(ctx.deps.last_proposals, choice)
        if selected is None and ctx.deps.last_proposals:
            # Never resolve a guess — silently delivering the wrong card is worse
            # than asking. The proposals stay live so the user can still pick.
            raise ModelRetry(
                "The user's message does not clearly match any of the shown proposals. "
                "If they are asking a question, answer it directly and invite them to "
                "pick by number or name. Only call get_recipe_details with a clear "
                "selection (a number 1-4 or one of the proposal names)."
            )
        try:
            found = await resolve_recipe(
                selected,
                choice,
                ctx.deps.onboarding,
                config=ctx.deps.config,
                site_filter=ctx.deps.search_site_filter,
                allow_ai_generated=ctx.deps.allow_ai_generated,
                usage=ctx.usage,
                agent_cache=sub_agents,
            )
        except ModelRetry:
            raise
        except Exception as exc:
            # Keep proposals/last_recipe untouched so the user can simply retry.
            log.exception("get_recipe_details_failed", choice=choice, error=str(exc))
            return FoundRecipe(
                recipe=Recipe(
                    name=choice,
                    description="",
                    ingredients=[],
                    steps=[],
                    prep_time_minutes=0,
                    cook_time_minutes=0,
                    difficulty="Easy",
                    servings=ctx.deps.onboarding.servings or 2,
                    tips=[],
                ),
                source="error",
            )
        # Multi-recipe page (STEP 45). The question belongs to the PAGE, not to
        # how the user reached it, so a picked proposal asks exactly as a pasted
        # link does. No card and no last_recipe yet — both wait for the answer.
        pending, resolved_recipe = await detect_split_verified(
            found.recipe, config=ctx.deps.config, usage=ctx.usage,
        )
        if pending is not None:
            ctx.deps.pending_split = pending
            ctx.deps.last_proposals = []
            log.info("get_recipe_details_split_question",
                     choice=choice, standalone=pending.standalone_names)
            return FoundRecipe(
                recipe=resolved_recipe,
                source=found.source,
                web_pick_fell_back=found.web_pick_fell_back,
                split_question=True,
                split_options=[b.name for b in pending.blocks],
            )

        ctx.deps.last_recipe = found                 # durable — used by add_to_calendar
        ctx.deps.last_proposals = []                 # clear so selection prompt doesn't repeat
        # Emit the recipe card only when there is a real recipe to show
        # (not_found/error placeholders stay silent).
        if found.source in ("web_search", "ai_generated"):
            ctx.deps.events.append(FinalRecipeEvent(recipe=found.recipe, source=found.source))
        return found

    @agent.tool
    async def get_recipe_from_url(
        ctx: RunContext[ChatAgentDeps],
        url: str,
        servings: int = 0,
    ) -> FoundRecipe:
        """Extract the recipe from a URL the user pasted, then show its card.

        Use this when the user provides a recipe LINK (not one of the proposals).
        After it returns the recipe card is shown; the user can then add it to the
        calendar. Do not call propose_recipes or run onboarding for a pasted link.

        Pass `servings` whenever the user says how many people it is for ("dla 8
        osób") — the quantities are rescaled to it. Omit it (0) if they didn't say.
        """
        cfg: TenantConfig = ctx.deps.config
        # The model retypes the URL into this argument and can corrupt long slugs,
        # producing a 404 and a bogus "page has no recipe". Recover the literal the
        # user actually pasted whenever this turn's message contains one.
        url = _url_from_user_message(url, ctx.deps.current_user_message)
        # Pin the URL into the tool so the sub-agent cannot retype (and corrupt)
        # it either. Per-URL agent, so deliberately NOT cached.
        fetch_agent = build_web_fetch_agent(cfg, pinned_url=url)
        recipe: Recipe | None = None
        try:
            # Extraction is occasionally flaky — retry once before giving up.
            for attempt in (1, 2):
                log.info("get_recipe_from_url_fetch", url=url, attempt=attempt)
                recipe = (await fetch_agent.run(web_fetch_prompt(url), usage=ctx.usage)).output
                if recipe is not None:
                    break
        except Exception as exc:
            log.exception("get_recipe_from_url_failed", url=url, error=str(exc))
            return FoundRecipe(
                recipe=Recipe(
                    name=url, description="", ingredients=[], steps=[],
                    prep_time_minutes=0, cook_time_minutes=0, difficulty="Easy",
                    servings=ctx.deps.onboarding.servings or 2, tips=[],
                ),
                source="error",
            )

        if recipe is None:
            # The page had no readable recipe — tell the user, show no card.
            return FoundRecipe(
                recipe=Recipe(
                    name=url, description="", ingredients=[], steps=[],
                    prep_time_minutes=0, cook_time_minutes=0, difficulty="Easy",
                    servings=ctx.deps.onboarding.servings or 2, tips=[],
                ),
                source="not_found",
            )

        # Preserve provenance (Rule 5): anchor the source link to the pasted URL if
        # the extractor didn't capture one.
        if not recipe.source_url:
            recipe.source_url = url

        # Persist the user's serving count BEFORE the split early-return below.
        # `deps.onboarding.servings` is the single anchor everything scales from
        # (STEP 46), and `choose_recipe_split` reads it on a LATER turn — so a
        # `return` that skips this write silently drops the count. Observed live:
        # "dodaj dla 8 osób z <url>" on the curry+naan page produced a calendar
        # entry stamped 4 portions, because the split path returned before the
        # write further down ever ran. Fill blanks only, never overwrite.
        if servings > 0 and not ctx.deps.onboarding.servings:
            ctx.deps.onboarding.servings = servings

        # Multi-recipe page (STEP 45)? Ask BEFORE committing to a card — emitting
        # the merged recipe and then asking would show exactly the 21-ingredient
        # result this step exists to prevent. Scaling is deliberately deferred
        # too: each dish is scaled after the user says which they want.
        pending, recipe = await detect_split_verified(
            recipe, config=cfg, usage=ctx.usage,
        )
        if pending is not None:
            ctx.deps.pending_split = pending
            ctx.deps.last_proposals = []
            log.info("get_recipe_from_url_split_question", url=url,
                     standalone=pending.standalone_names)
            return FoundRecipe(
                recipe=recipe,
                source="web_search",
                split_question=True,
                split_options=[b.name for b in pending.blocks],
            )

        # Scaling is a SEPARATE step from the verbatim extraction above (Rule 5).
        # The page states its OWN serving count; the user may want a different
        # number ("dla 4 osób"). Only when the two differ do we rescale — and
        # original_servings records what the page said, so the recipe card and the
        # shopping list can tell "this recipe is for N" from "you asked for M".
        # A pasted link skips BOTH paths that normally record the serving count:
        # proposals never happen, and the prompt tells the model not to run
        # onboarding for a link. So a one-shot "dodaj dla 8 osób z <url>" left
        # onboarding.servings empty and scaling silently never ran — the entry
        # then carried the page's own amounts under the user's requested count.
        # Take the tool argument as the primary source and persist it, the same
        # way propose_recipes does (STEP 46).
        if servings > 0 and not ctx.deps.onboarding.servings:
            ctx.deps.onboarding.servings = servings
        target_servings = servings or ctx.deps.onboarding.servings or 0
        if target_servings > 0:
            scale_agent = _cached_agent(sub_agents, "recipe_scale", build_recipe_scale_agent, cfg)
            before = recipe.servings
            recipe = await scale_recipe_to_servings(
                recipe, target_servings, agent=scale_agent, usage=ctx.usage,
            )
            if recipe.servings != before:
                log.info("get_recipe_from_url_scaled", url=url,
                         original_servings=recipe.original_servings,
                         target_servings=recipe.servings)

        found = FoundRecipe(recipe=recipe, source="web_search")
        ctx.deps.last_recipe = found                 # durable — used by add_to_calendar
        ctx.deps.last_proposals = []                 # a pasted link isn't a selection
        ctx.deps.events.append(FinalRecipeEvent(recipe=recipe, source="web_search"))
        return found

    @agent.tool
    async def choose_recipe_split(
        ctx: RunContext[ChatAgentDeps],
        mode: str,
    ) -> ChooseSplitResult:
        """Answer the "one recipe or two?" question about a multi-recipe page.

        Call this ONLY after a previous tool reported split_question=true and you
        asked the user. mode="split" sends a separate recipe card per dish;
        mode="together" sends one combined card.
        """
        pending = ctx.deps.pending_split
        if pending is None:
            # Rule 7: contain it. The model can reach for this tool with nothing
            # pending — say so structurally rather than raising mid-turn.
            return ChooseSplitResult(
                mode=mode,
                recipe_count=0,
                message="No multi-recipe page is awaiting a choice — ignore this "
                        "and continue the conversation normally.",
            )

        cfg: TenantConfig = ctx.deps.config
        want_split = mode.strip().lower() != "together"
        recipes = split_into_recipes(pending) if want_split else [merged_recipe(pending)]

        # Scaling was deferred until now (Rule 5: it is separate from extraction,
        # and there was no point scaling a recipe the user might discard). Each
        # dish scales from its OWN stated count — the naan's 8 is not the curry's
        # 4 — so a single target would be wrong for one of them. We only scale the
        # MAIN dish to the user's target: they asked for "curry for 4", never for
        # "naan for 4", and rescaling a side dish nobody asked about is the same
        # class of silent edit this step is fixing.
        target = ctx.deps.onboarding.servings or 0
        if target > 0 and recipes:
            scale_agent = _cached_agent(sub_agents, "recipe_scale", build_recipe_scale_agent, cfg)
            try:
                recipes[0] = await scale_recipe_to_servings(
                    recipes[0], target, agent=scale_agent, usage=ctx.usage,
                )
            except Exception as exc:
                # Unscaled amounts with a truthful serving count beat no recipe.
                log.exception("choose_recipe_split_scale_failed", error=str(exc))

        for recipe in recipes:
            ctx.deps.events.append(FinalRecipeEvent(recipe=recipe, source="web_search"))

        # add_to_calendar trusts last_recipe (STEP 49) — point it at the main dish,
        # the one the user is most likely to add next.
        ctx.deps.last_recipe = FoundRecipe(recipe=recipes[0], source="web_search")
        ctx.deps.pending_split = None                # answered — never re-ask
        log.info("choose_recipe_split", mode=mode, count=len(recipes))

        return ChooseSplitResult(
            mode="split" if want_split else "together",
            recipe_count=len(recipes),
            recipe_names=[r.name for r in recipes],
            message="Recipe cards sent to the user — do NOT retell them; invite "
                    "the next step (adding to the calendar).",
        )

    @agent.tool
    async def add_to_calendar(
        ctx: RunContext[ChatAgentDeps],
        recipe_name: str,
        ingredients: list[str],
        target_date: str,
        meal_slot: MealSlot = MealSlot.OBIAD,
    ) -> CalendarAddResult:
        """Add a recipe to the meal calendar on target_date (YYYY-MM-DD).

        meal_slot places the dish in one of the day's meal sections; pass the one
        the user named (śniadanie / lunch / obiad / kolacja), or leave the default.
        """
        # Normalise the date to strict YYYY-MM-DD — the frontend calendar matches
        # day cells by exact string, so "2026-06-4" or "4.06" would silently fail.
        norm_date = _normalize_date(target_date)
        # Attach the full recipe from the last find_recipe call so the frontend
        # can show a detail modal when the user clicks the calendar entry.
        recipe_dict: dict | None = None
        entry_ingredients = ingredients
        servings: int | None = None
        source_servings: int | None = None

        resolved = ctx.deps.last_recipe
        if resolved is not None:
            recipe_dict = resolved.recipe.model_dump()

        if resolved is not None and _same_dish(resolved.recipe.name, recipe_name):
            # STEP 49: the resolved recipe is the authority for BOTH the amounts
            # and the portion count.
            #
            # `ingredients` above is a MODEL-GENERATED argument — the LLM retypes
            # the list into the tool call and can hand back the pre-scale amounts
            # while `last_recipe` holds the ones RecipeScaleAgent actually
            # produced. Stamping a portion count onto that stale list would make
            # the number describe quantities it doesn't match, which is worse for
            # the user than showing no number at all.
            if resolved.recipe.ingredients:
                entry_ingredients = list(resolved.recipe.ingredients)
            servings = resolved.recipe.servings or None
            source_servings = resolved.recipe.original_servings
        else:
            # No resolved recipe for this dish (e.g. typed straight into the
            # calendar): fall back to what the user told us during onboarding.
            # Nothing was scaled from a page, so there is no source count.
            servings = ctx.deps.onboarding.servings or None

        entry = CalendarEntry(
            id=str(uuid.uuid4()),
            date=norm_date,
            recipe_name=recipe_name,
            ingredients=entry_ingredients,
            recipe=recipe_dict,
            meal_slot=meal_slot,
            servings=servings,
            source_servings=source_servings,
        )
        # Guard against emitting the same entry id twice in one turn.
        already = {
            ev.entry.id for ev in ctx.deps.events if isinstance(ev, CalendarAddEvent)
        }
        if entry.id not in already:
            ctx.deps.events.append(CalendarAddEvent(entry=entry))
        return CalendarAddResult(
            entry_id=entry.id,
            date=norm_date,
            recipe_name=recipe_name,
            meal_slot=meal_slot,
            servings=entry.servings,
            source_servings=entry.source_servings,
        )

    @agent.tool
    async def remove_from_calendar(
        ctx: RunContext[ChatAgentDeps],
        entry_id: str,
    ) -> CalendarRemoveResult:
        """Remove a meal from the calendar by its ID."""
        exists = any(e.id == entry_id for e in ctx.deps.calendar.entries)
        if exists:
            ctx.deps.events.append(CalendarRemoveEvent(entry_id=entry_id))
        return CalendarRemoveResult(removed=exists, entry_id=entry_id)

    @agent.tool
    async def get_shopping_list(
        ctx: RunContext[ChatAgentDeps],
        date_from: str,
        date_to: str,
    ) -> ShoppingListResult:
        """Build a structured shopping list from calendar entries between date_from and
        date_to (inclusive, YYYY-MM-DD)."""
        in_range = [
            e for e in ctx.deps.calendar.entries
            if date_from <= e.date <= date_to
        ]
        all_ingredients = [ing for e in in_range for ing in e.ingredients]
        if not all_ingredients:
            ctx.deps.events.append(ShoppingListEvent(shopping_list=ShoppingList(items=[], sections=[])))
            return ShoppingListResult(item_count=0, sections=[], date_from=date_from, date_to=date_to)

        raw_text = "\n".join(all_ingredients)
        sl_agent = _cached_agent(sub_agents, "shopping_list", build_shopping_list_agent, ctx.deps.config)
        try:
            shopping_list: ShoppingList = (await sl_agent.run(raw_text, usage=ctx.usage)).output
        except Exception as exc:
            log.exception("get_shopping_list_failed", error=str(exc))
            return ShoppingListResult(
                item_count=0,
                sections=[],
                date_from=date_from,
                date_to=date_to,
                error="temporary failure while building the list — apologise briefly "
                      "and ask the user to try again in a moment.",
            )
        # Pantry subtraction (STEP 51) — deterministic Python AFTER the agent, so
        # the agent keeps its single job and this costs no extra tokens. Skipped
        # entirely (byte-identical output) unless the user turned the flag on.
        covered: list[str] = []
        reduced: list[str] = []
        flagged: list[str] = []
        if ctx.deps.subtract_pantry and ctx.deps.pantry:
            ui = ctx.deps.config.ui
            aware = subtract_pantry_from_list(
                shopping_list,
                ctx.deps.pantry,
                note_have=ui.pantry_note_have,
                note_check=ui.pantry_note_check,
            )
            shopping_list = aware.shopping_list
            covered = aware.covered
            reduced = [r.item.name for r in aware.results if r.outcome is PantryOutcome.REDUCED]
            flagged = [r.item.name for r in aware.results if r.outcome is PantryOutcome.FLAGGED]
            log.info("shopping_list_pantry_subtracted",
                     covered=len(covered), reduced=len(reduced), flagged=len(flagged))

        ctx.deps.events.append(ShoppingListEvent(shopping_list=shopping_list))
        return ShoppingListResult(
            item_count=len(shopping_list.items),
            sections=shopping_list.sections,
            date_from=date_from,
            date_to=date_to,
            pantry_subtracted=bool(covered or reduced or flagged),
            pantry_covered=covered,
            pantry_reduced=reduced,
            pantry_flagged=flagged,
        )

    return agent


# ── Streaming helper ──────────────────────────────────────────────────────────

@asynccontextmanager
async def stream_chat_response(
    agent: Agent[ChatAgentDeps, str],
    deps: ChatAgentDeps,
    message_history: list[Any],
    user_message: str,
) -> AsyncIterator[AsyncIterator[str]]:
    """
    Async context manager for one chat turn.

    Usage:
        async with stream_chat_response(agent, deps, history, text) as tokens:
            async for token in tokens:
                ...
        # After the block: history is updated in-place with this turn's messages.

    Side-effects are readable from deps.events (ordered) after the block.

    The whole turn — chat requests plus every sub-agent call made from tools
    (which pass usage=ctx.usage) — shares one usage budget, capped by the
    tenant's UsageLimits so a runaway tool loop cannot burn unbounded tokens.
    """
    config = deps.config
    # Expose the raw message to tools so they can recover exact literals (URLs)
    # rather than trusting the model's retyped tool arguments.
    deps.current_user_message = user_message
    usage_limits = UsageLimits(
        request_limit=config.usage_request_limit,
        total_tokens_limit=config.usage_total_tokens_limit,
    )
    async with agent.run_stream(
        user_message,
        deps=deps,
        message_history=message_history,
        usage_limits=usage_limits,
    ) as result:
        yield result.stream_text(delta=True)
        # Extend history in-place so caller's list reference is updated
        message_history.extend(result.new_messages())
        usage = result.usage
        # Surface this turn's spend so the WS handler can meter it against the
        # user's daily/monthly quota (per-turn output — cleared by reset_turn).
        deps.last_turn_total_tokens = usage.total_tokens or 0
        # Per-turn cost attribution (includes sub-agent calls via shared usage).
        log.info(
            "chat_turn_usage",
            tenant=config.tenant_id,
            requests=usage.requests,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
        )
