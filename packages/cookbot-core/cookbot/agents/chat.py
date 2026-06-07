"""
Guided conversational chat agent.

Architecture
------------
One agent instance per WebSocket connection (built once, reused across turns).
Deps instance is also per-connection — onboarding state accumulates there across
turns.  Per-turn side-effect fields (calendar_adds, calendar_removes,
shopping_list_items) are reset at the start of each turn by the WS handler.

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
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from cookbot.agents.recipe_gen import build_recipe_gen_agent, recipe_gen_prompt
from cookbot.agents.recipe_options import build_recipe_options_agent, recipe_options_prompt
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
    source: str  # "web_search" | "ai_generated"


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


# ── Agent deps (one instance per WS connection) ───────────────────────────────

class ChatAgentDeps(BaseModel):
    """
    One instance per WebSocket connection. Fields fall into three lifetimes:

    1. Connection-durable — created once, survive every turn (do NOT reset).
    2. Per-turn input — refreshed by the WS handler at the start of each turn
       from the message payload / user's Firestore prefs.
    3. Per-turn output collectors — written by tools during a turn, drained into
       WS messages after it, then cleared by reset_turn() before the next turn.

    The reset contract lives in reset_turn() (called by the WS handler), NOT as
    loose lines scattered in the handler — add a collector here and to reset_turn()
    together so the two never drift.
    """
    model_config = {"arbitrary_types_allowed": True}

    # ── 1. Connection-durable ────────────────────────────────────────────────
    config: Any                                        # TenantConfig
    onboarding: OnboardingState = OnboardingState()    # accumulates until complete
    last_recipe: FoundRecipe | None = None             # set by get_recipe_details, used by add_to_calendar
    last_proposals: list[RecipeSummary] = []           # proposals (1-4), consumed by get_recipe_details

    # ── 2. Per-turn input (refreshed each turn by the WS handler) ─────────────
    calendar: CalendarState = CalendarState()          # current calendar from the WS payload
    search_site_filter: str = ""                       # e.g. "site:kwestiasmaku.com OR site:aniagotuje.pl"
    allow_ai_generated: bool = True                    # when False, skip RecipeGenAgent fallback

    # ── 3. Per-turn output collectors (cleared by reset_turn) ─────────────────
    recipe_ready_this_turn: bool = False               # set by get_recipe_details
    calendar_adds: list[CalendarEntry] = []
    calendar_removes: list[str] = []
    shopping_list_items: ShoppingList | None = None
    recipe_options: list[RecipeSummary] = []           # set by propose_recipes

    def reset_turn(self) -> None:
        """Clear all per-turn output collectors. Call once at the start of every
        WS turn, before streaming the agent response. Connection-durable and
        per-turn-input fields are intentionally left untouched."""
        self.recipe_ready_this_turn = False
        self.calendar_adds = []
        self.calendar_removes = []
        self.shopping_list_items = None
        self.recipe_options = []


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
        if selected.source_url:
            # URL already known from options step — fetch directly, no second search
            log.info("get_recipe_details_fetch_known_url", url=selected.source_url)
            fetch_agent = build_web_fetch_agent(config)
            recipe = (await fetch_agent.run(
                web_fetch_prompt(selected.source_url, servings)
            )).output
        else:
            # No URL known — fall back to a new search by recipe name
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

    return FoundRecipe(recipe=recipe, source=source)


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
            today = datetime.now(ZoneInfo("Europe/Warsaw")).date().isoformat()
        except Exception:
            # tzdata not installed — fall back to UTC+2 (CET+1/CEST offset)
            today = datetime.now(timezone(timedelta(hours=2))).date().isoformat()
        return f"## Calendar\nToday is {today}. Use this exact date when the user says 'today' or 'dzisiaj'."

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
        dietary_hints: list[str] = [],
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
            dietary_hints=dietary_hints,
            missing_staples=[],
        )
        opts_agent = build_recipe_options_agent(cfg)
        result = await opts_agent.run(
            recipe_options_prompt(
                parsed, intent,
                site_filter=ctx.deps.search_site_filter,
                allow_ai_generated=ctx.deps.allow_ai_generated,
            )
        )
        proposals = result.output.proposals[:4]
        ctx.deps.last_proposals = proposals
        ctx.deps.recipe_options = proposals
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
        ctx.deps.last_recipe = found
        ctx.deps.recipe_ready_this_turn = True
        ctx.deps.last_proposals = []  # clear so selection prompt doesn't repeat
        return found

    @agent.tool
    async def add_to_calendar(
        ctx: RunContext[ChatAgentDeps],
        recipe_name: str,
        ingredients: list[str],
        target_date: str,
    ) -> CalendarAddResult:
        """Add a recipe to the meal calendar on target_date (YYYY-MM-DD)."""
        # Attach the full recipe from the last find_recipe call so the frontend
        # can show a detail modal when the user clicks the calendar entry.
        recipe_dict: dict | None = None
        if ctx.deps.last_recipe is not None:
            recipe_dict = ctx.deps.last_recipe.recipe.model_dump()
        entry = CalendarEntry(
            id=str(uuid.uuid4()),
            date=target_date,
            recipe_name=recipe_name,
            ingredients=ingredients,
            recipe=recipe_dict,
        )
        ctx.deps.calendar_adds.append(entry)
        return CalendarAddResult(entry_id=entry.id, date=target_date, recipe_name=recipe_name)

    @agent.tool
    async def remove_from_calendar(
        ctx: RunContext[ChatAgentDeps],
        entry_id: str,
    ) -> CalendarRemoveResult:
        """Remove a meal from the calendar by its ID."""
        exists = any(e.id == entry_id for e in ctx.deps.calendar.entries)
        if exists:
            ctx.deps.calendar_removes.append(entry_id)
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
            ctx.deps.shopping_list_items = ShoppingList(items=[], sections=[])
            return ShoppingListResult(item_count=0, sections=[], date_from=date_from, date_to=date_to)

        raw_text = "\n".join(all_ingredients)
        sl_agent = build_shopping_list_agent(ctx.deps.config)
        shopping_list: ShoppingList = (await sl_agent.run(raw_text)).output
        ctx.deps.shopping_list_items = shopping_list
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

    Side-effects (calendar_adds etc.) are readable from deps after the block.
    """
    async with agent.run_stream(
        user_message,
        deps=deps,
        message_history=message_history,
    ) as result:
        yield result.stream_text(delta=True)
        # Extend history in-place so caller's list reference is updated
        message_history.extend(result.new_messages())
