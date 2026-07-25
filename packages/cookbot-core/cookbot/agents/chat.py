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
from cookbot.agents.web_search import build_web_fetch_agent, build_web_search_agent, web_fetch_prompt, web_search_prompt
from cookbot.models.calendar import CalendarEntry, CalendarState
from cookbot.models.recipe import ParsedIngredients, Recipe, RecipeSummary, UserIntent
from cookbot.models.shopping import ShoppingList
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


class CalendarAddResult(BaseModel):
    entry_id: str
    date: str
    recipe_name: str


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

    # ── 2. Per-turn input (refreshed each turn by the WS handler) ─────────────
    calendar: CalendarState = field(default_factory=CalendarState)  # current calendar from the WS payload
    search_site_filter: str = ""                       # hard site: restriction (sites_only mode only)
    preferred_sites: list[str] = field(default_factory=list)  # soft-prefer domains (sites_and_internet mode)
    allow_ai_generated: bool = True                    # when False, skip RecipeGenAgent fallback

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


def dump_chat_state(
    deps: ChatAgentDeps, message_history: list[ModelMessage]
) -> dict[str, Any]:
    """Serialize the resumable conversation state to a Firestore-safe dict."""
    state = ChatState(
        messages_json=ModelMessagesTypeAdapter.dump_json(message_history).decode(),
        onboarding=deps.onboarding,
        last_recipe=deps.last_recipe,
        last_proposals=deps.last_proposals,
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
    lower = choice_stripped.lower()
    for p in proposals:
        if lower in p.name.lower() or p.name.lower() in lower:
            return p
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


# ── Dynamic onboarding system prompt (module-level so it's unit-testable) ─────

def onboarding_status_prompt(
    ob: OnboardingState,
    questions: list[str],
    *,
    last_proposals: list[RecipeSummary],
    last_recipe: FoundRecipe | None,
) -> str:
    """Build the per-turn onboarding/routing system prompt.

    Three branches: recipe-selection (proposals shown, awaiting pick), the direct
    recipe fast path (user named a concrete dish → search now), and guided
    onboarding (vague request → ask the next missing field)."""
    if ob.complete:
        # If proposals were sent and the user is picking, mandate the tool call.
        if last_proposals and last_recipe is None:
            names = "\n".join(f"  {i+1}. {p.name}" for i, p in enumerate(last_proposals))
            return f"""## RECIPE SELECTION IN PROGRESS
The user has been shown these recipe options:
{names}

If the user's message picks one of them (a number like "1" or a recipe name),
call get_recipe_details immediately with their choice. Do not describe the
chosen recipe — the full recipe card is displayed automatically after the tool
call. If the message is instead a question or comment about the options, answer
it directly and invite them to pick one by number or name.
"""
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
- Add a meal to the calendar → call add_to_calendar.
- Remove a meal from the calendar → call remove_from_calendar.
- Build a shopping list for a date range → call get_shopping_list.
- Answer general cooking questions directly.

## Recipe flow
0a. If the user PASTES A RECIPE LINK (a URL) and wants that recipe or to add it,
   call get_recipe_from_url with the link — do NOT call propose_recipes or run
   onboarding. The recipe card is shown automatically; then offer to add it to the
   calendar. If it returns source="not_found" or "error", no card is shown —
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
   Just confirm with one short sentence (e.g. "Oto przepis! Dodać do kalendarza?") and offer
   to add it to the calendar or find more recipes for other days.
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

## After the first recipe — free-chat mode
Once a recipe has been delivered, stay in free-chat mode indefinitely:
- User can ask for another recipe for a different day → call propose_recipes again
  (no need to re-run onboarding; reuse the same preferences or ask only what changed).
- User can ask to add the current recipe to another date → call add_to_calendar.
- User can ask about shopping, substitutions, cooking tips → answer directly.
- If the user says "nowa rozmowa", "od nowa", "reset" or similar → acknowledge and
  tell them to use the restart button, or simply forget the context and start fresh
  by treating their next message as a brand-new request.

## Response style
- Be concise, warm, and practical.
- After propose_recipes, briefly invite the user to pick ("Który Cię interesuje?").
- After get_recipe_details, one short sentence only — the card is shown automatically.
- After adding to calendar, confirm the date and suggest next steps.
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
            f"- If that date has already passed this year, still use {year} unless the user says otherwise."
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
    ) -> FoundRecipe:
        """Extract the recipe from a URL the user pasted, then show its card.

        Use this when the user provides a recipe LINK (not one of the proposals).
        After it returns the recipe card is shown; the user can then add it to the
        calendar. Do not call propose_recipes or run onboarding for a pasted link.
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

        # Scaling is a SEPARATE step from the verbatim extraction above (Rule 5).
        # The page states its OWN serving count; the user may want a different
        # number ("dla 4 osób"). Only when the two differ do we rescale — and
        # original_servings records what the page said, so the recipe card and the
        # shopping list can tell "this recipe is for N" from "you asked for M".
        target_servings = ctx.deps.onboarding.servings or 0
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
    async def add_to_calendar(
        ctx: RunContext[ChatAgentDeps],
        recipe_name: str,
        ingredients: list[str],
        target_date: str,
    ) -> CalendarAddResult:
        """Add a recipe to the meal calendar on target_date (YYYY-MM-DD)."""
        # Normalise the date to strict YYYY-MM-DD — the frontend calendar matches
        # day cells by exact string, so "2026-06-4" or "4.06" would silently fail.
        norm_date = _normalize_date(target_date)
        # Attach the full recipe from the last find_recipe call so the frontend
        # can show a detail modal when the user clicks the calendar entry.
        recipe_dict: dict | None = None
        if ctx.deps.last_recipe is not None:
            recipe_dict = ctx.deps.last_recipe.recipe.model_dump()
        entry = CalendarEntry(
            id=str(uuid.uuid4()),
            date=norm_date,
            recipe_name=recipe_name,
            ingredients=ingredients,
            recipe=recipe_dict,
        )
        # Guard against emitting the same entry id twice in one turn.
        already = {
            ev.entry.id for ev in ctx.deps.events if isinstance(ev, CalendarAddEvent)
        }
        if entry.id not in already:
            ctx.deps.events.append(CalendarAddEvent(entry=entry))
        return CalendarAddResult(entry_id=entry.id, date=norm_date, recipe_name=recipe_name)

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
        ctx.deps.events.append(ShoppingListEvent(shopping_list=shopping_list))
        return ShoppingListResult(
            item_count=len(shopping_list.items),
            sections=shopping_list.sections,
            date_from=date_from,
            date_to=date_to,
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
