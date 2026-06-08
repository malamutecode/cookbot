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

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from cookbot.agents.recipe_gen import build_recipe_gen_agent, recipe_gen_prompt
from cookbot.agents.recipe_options import (
    build_recipe_options_agent,
    populate_proposal_images,
    recipe_options_prompt,
)
from cookbot.agents.shopping_list import build_shopping_list_agent
from cookbot.agents.web_search import build_web_fetch_agent, build_web_search_agent, web_fetch_prompt, web_search_prompt
from cookbot.models.calendar import CalendarEntry, CalendarState
from cookbot.models.recipe import ParsedIngredients, Recipe, RecipeSummary, UserIntent
from cookbot.models.shopping import ShoppingList
from cookbot.models.tenant import TenantConfig

log = structlog.get_logger()


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
    source: str  # "web_search" | "ai_generated" | "not_found"
    # True when the user picked a WEB proposal but the page couldn't be read, so
    # the content was AI-generated instead. The chat agent tells the user.
    web_pick_fell_back: bool = False


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

class ChatAgentDeps(BaseModel):
    """
    One instance per WebSocket connection. Fields fall into three lifetimes:

    1. Connection-durable — created once, survive every turn (do NOT reset).
    2. Per-turn input — refreshed by the WS handler at the start of each turn
       from the message payload / user's Firestore prefs.
    3. Per-turn output — `events`, an ordered list of side-effects appended by
       tools as they run, drained into WS messages by the handler after the turn,
       then cleared by reset_turn() before the next turn.

    The reset contract lives in reset_turn() (called by the WS handler), NOT as
    loose lines scattered in the handler — add a per-turn field here and to
    reset_turn() together so the two never drift.
    """
    model_config = {"arbitrary_types_allowed": True}

    # ── 1. Connection-durable ────────────────────────────────────────────────
    config: Any                                        # TenantConfig
    onboarding: OnboardingState = OnboardingState()    # accumulates until complete
    last_recipe: FoundRecipe | None = None             # set by get_recipe_details, used by add_to_calendar
    last_proposals: list[RecipeSummary] = []           # proposals (1-4), consumed by get_recipe_details

    # ── 2. Per-turn input (refreshed each turn by the WS handler) ─────────────
    calendar: CalendarState = CalendarState()          # current calendar from the WS payload
    search_site_filter: str = ""                       # hard site: restriction (sites_only mode only)
    preferred_sites: list[str] = []                    # soft-prefer domains (sites_and_internet mode)
    allow_ai_generated: bool = True                    # when False, skip RecipeGenAgent fallback

    # ── 3. Per-turn output (cleared by reset_turn) ────────────────────────────
    # Ordered side-effects, appended by tools in call order, drained by the WS handler.
    events: list[TurnEvent] = []

    def reset_turn(self) -> None:
        """Clear the per-turn output events. Call once at the start of every WS
        turn, before streaming the agent response. Connection-durable and
        per-turn-input fields are intentionally left untouched."""
        self.events = []


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
    from datetime import datetime, timezone, timedelta  # noqa: PLC0415

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

    Falls back to the first proposal if nothing matches but proposals exist.
    Returns None only when there are no proposals at all.
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
    return proposals[0]


async def resolve_recipe(
    selected: RecipeSummary | None,
    choice: str,
    ob: OnboardingState,
    *,
    config: TenantConfig,
    site_filter: str,
    allow_ai_generated: bool,
) -> FoundRecipe:
    """Resolve a chosen proposal to a full Recipe.

    Decision tree (unchanged from the original get_recipe_details body):
      1. web_search proposal with a known URL → fetch that URL directly.
      2. web_search proposal without a URL → search by name (retry without the
         site filter if the filtered search finds nothing).
      3. Nothing found yet and AI allowed → generate with RecipeGenAgent.
      4. Nothing found and AI disabled → a "not_found" placeholder Recipe.
    """
    recipe: Recipe | None = None

    if selected and selected.source == "web_search":
        servings = ob.servings or 2

        # 1. If the proposal knows its URL, fetch that page directly.
        if selected.source_url:
            log.info("get_recipe_details_fetch_known_url", url=selected.source_url)
            fetch_agent = build_web_fetch_agent(config)
            recipe = (await fetch_agent.run(
                web_fetch_prompt(selected.source_url, servings)
            )).output

        # 2. If we have no recipe yet (no URL, or the fetch failed to extract),
        #    search the web by recipe name. The user picked a WEB option, so we
        #    keep trying the web before any AI fallback.
        if recipe is None:
            if selected.source_url:
                log.info("get_recipe_details_fetch_failed_searching", url=selected.source_url)
            ws_intent = UserIntent(
                dish_type=selected.name,
                servings=servings,
                max_time_minutes=0,
                available_ingredients=[],
                free_notes="",
            )
            ws_parsed = ParsedIngredients(items=[], must_use=[], dietary_hints=[], missing_staples=[])
            ws_agent = build_web_search_agent(config)
            recipe = (await ws_agent.run(
                web_search_prompt(ws_parsed, ws_intent, site_filter)
            )).output
            if recipe is None and site_filter:
                log.info("get_recipe_details_retry_no_filter", recipe_name=selected.name)
                recipe = (await ws_agent.run(
                    web_search_prompt(ws_parsed, ws_intent, site_filter="")
                )).output
        # Provenance safety net: a web recipe must carry a source_url. If the
        # extractor omitted it but the proposal knew the URL, backfill it so the
        # frontend always shows a "Źródło" link for web recipes.
        if recipe is not None and not recipe.source_url and selected.source_url:
            recipe.source_url = selected.source_url
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
        gen_agent = build_recipe_gen_agent(config)
        recipe = (await gen_agent.run(recipe_gen_prompt(parsed, intent))).output
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
        from datetime import datetime, timezone, timedelta  # noqa: PLC0415
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
    # been collected and what the model MUST do next.
    @agent.system_prompt
    async def _onboarding_status(ctx: RunContext[ChatAgentDeps]) -> str:
        ob = ctx.deps.onboarding
        if ob.complete:
            # If proposals were sent and user is picking, mandate the tool call.
            if ctx.deps.last_proposals and ctx.deps.last_recipe is None:
                names = "\n".join(
                    f"  {i+1}. {p.name}" for i, p in enumerate(ctx.deps.last_proposals)
                )
                return f"""## RECIPE SELECTION IN PROGRESS
The user has been shown these recipe options:
{names}

MANDATORY: The user's message is their selection. Call get_recipe_details immediately
with their choice (a number like "1" or the recipe name). Do not describe any recipe —
the full recipe card is displayed automatically after the tool call.
"""
            return ""  # onboarding done, no extra instructions needed

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

        next_field, next_question = next(
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
2. After update_onboarding returns, if complete=false: ask ONLY the next missing question.
3. If complete=true: call propose_recipes immediately using the collected values.
4. NEVER re-ask a question whose field is already listed in "Collected so far".
"""

    # ── Tools ────────────────────────────────────────────────────────────────

    @agent.tool
    async def update_onboarding(
        ctx: RunContext[ChatAgentDeps],
        dish_type: str | None = None,
        servings: int | None = None,
        max_time_minutes: int | None = None,
        ingredients: list[str] | None = None,
        free_notes: str | None = None,
    ) -> dict[str, Any]:
        """Record one or more onboarding answers. Always call this before asking the next question."""
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
        return {
            "complete": ob.complete,
            "next_missing_field": ob.next_missing_field(),
            "collected": ob.model_dump(),
        }

    @agent.tool
    async def propose_recipes(
        ctx: RunContext[ChatAgentDeps],
        dish_type: str,
        ingredients: list[str],
        max_time_minutes: int = 0,
        servings: int = 2,
        dietary_hints: list[str] | None = None,
        free_notes: str = "",
    ) -> dict[str, Any]:
        """Propose 4 recipe options. Call after onboarding is complete or on explicit user request. The options are sent to the frontend automatically."""
        cfg: TenantConfig = ctx.deps.config
        ob = ctx.deps.onboarding
        intent = UserIntent(
            dish_type=dish_type or ob.dish_type or "any",
            servings=servings or ob.servings or 2,
            max_time_minutes=max_time_minutes or ob.max_time_minutes or 0,
            available_ingredients=ingredients or ob.ingredients or [],
            free_notes=free_notes or ob.free_notes or "",
        )
        parsed = ParsedIngredients(
            items=intent.available_ingredients,
            must_use=[],
            dietary_hints=dietary_hints or [],
            missing_staples=[],
        )
        opts_agent = build_recipe_options_agent(cfg)
        result = await opts_agent.run(
            recipe_options_prompt(
                parsed, intent,
                site_filter=ctx.deps.search_site_filter,
                allow_ai_generated=ctx.deps.allow_ai_generated,
                preferred_sites=ctx.deps.preferred_sites,
            )
        )
        proposals = result.output.proposals[:4]
        # Best-effort: fill dish images from each web page's og:image (concurrent,
        # never blocks the result — failures leave image_url=None for a placeholder).
        await populate_proposal_images(proposals)
        ctx.deps.last_proposals = proposals          # durable — consumed by get_recipe_details
        ctx.deps.events.append(RecipeOptionsEvent(proposals=proposals))
        return {
            "count": len(proposals),
            "names": [p.name for p in proposals],
            "message": "Options sent to user — ask them to pick one by number or name.",
        }

    @agent.tool
    async def get_recipe_details(
        ctx: RunContext[ChatAgentDeps],
        choice: str,
    ) -> FoundRecipe:
        """Get the full recipe for the option the user chose. choice is a number (1-4) or the recipe name."""
        selected = _select_proposal(ctx.deps.last_proposals, choice)
        found = await resolve_recipe(
            selected,
            choice,
            ctx.deps.onboarding,
            config=ctx.deps.config,
            site_filter=ctx.deps.search_site_filter,
            allow_ai_generated=ctx.deps.allow_ai_generated,
        )
        ctx.deps.last_recipe = found                 # durable — used by add_to_calendar
        ctx.deps.last_proposals = []                 # clear so selection prompt doesn't repeat
        # Emit the recipe card unless nothing was found (placeholder stays silent).
        if found.source != "not_found":
            ctx.deps.events.append(FinalRecipeEvent(recipe=found.recipe, source=found.source))
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
        """Build a structured shopping list from calendar entries between date_from and date_to (inclusive, YYYY-MM-DD)."""
        in_range = [
            e for e in ctx.deps.calendar.entries
            if date_from <= e.date <= date_to
        ]
        all_ingredients = [ing for e in in_range for ing in e.ingredients]
        if not all_ingredients:
            ctx.deps.events.append(ShoppingListEvent(shopping_list=ShoppingList(items=[], sections=[])))
            return ShoppingListResult(item_count=0, sections=[], date_from=date_from, date_to=date_to)

        raw_text = "\n".join(all_ingredients)
        sl_agent = build_shopping_list_agent(ctx.deps.config)
        shopping_list: ShoppingList = (await sl_agent.run(raw_text)).output
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
    """
    async with agent.run_stream(
        user_message,
        deps=deps,
        message_history=message_history,
    ) as result:
        yield result.stream_text(delta=True)
        # Extend history in-place so caller's list reference is updated
        message_history.extend(result.new_messages())
