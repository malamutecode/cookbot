# agents/ — Agentic Architecture

> Deep reference for everything in `cookbot/agents/`. The root
> [CLAUDE.md](../../../../CLAUDE.md) carries only a summary of this — read this
> file before touching any agent. Global invariants (the 5 Architecture Rules,
> the agent factory pattern, `TestModel` in tests) live in the root; this file
> is the module-specific detail.

The product is driven by **one orchestrating ChatAgent** that owns the
conversation and delegates narrow tasks to **stateless sub-agents** via tools.
This replaced the original rigid 5-step pipeline (see [TASK.md](../../../../TASK.md)).

## The shape

```
                    ┌──────────────────────────────────────┐
   WebSocket turn → │            ChatAgent                 │ ← conversation leader
                    │  (1 instance per WS connection)      │   • intent recognition / routing
                    │  output_type=str (streamed tokens)   │   • guided onboarding (not a form)
                    │  deps=ChatAgentDeps (per-connection) │   • free-chat after first recipe
                    └──────────────┬───────────────────────┘
                                   │ calls as @agent.tool
        ┌──────────────┬──────────────┼───────────────┬──────────────────┐
        ▼              ▼              ▼               ▼                  ▼
  propose_recipes  get_recipe_    add_to_calendar  get_shopping_list  update_onboarding
        │           details        remove_from_…     │                  (state only)
        │           get_recipe_   choose_recipe_     │
        │           from_url        split            │
        ▼              ▼                             ▼
  fast path OR    WebSearch / WebFetch          ShoppingList
  RecipeOptions    / RecipeGen Agent               Agent
     Agent        (full Recipe extract/gen)     (dedup + sections)
  (6 zero-LLM /   then RecipeScaleAgent
   4 summaries)
```

`get_recipe_details` resolves a *proposal* (by index or name, via
`resolve_recipe`); `get_recipe_from_url` handles a **pasted link** and takes an
explicit `servings` argument — see "Servings" below for why it cannot read the
count from onboarding.

**ChatAgent is the only stateful, conversational agent.** Every sub-agent is a
single-LLM-call, stateless function built by a `build_*_agent(config)` factory
and invoked from inside a ChatAgent tool. Sub-agents never talk to each other —
the ChatAgent coordinates them.

## Responsibilities of the ChatAgent

| Responsibility | How it's implemented |
|---|---|
| Intent recognition / routing | LLM picks which tool to call from the user's message |
| Guided (non-rigid) onboarding | `update_onboarding` tool fills 5 fields; dynamic system prompt drives the next question; user can skip/fill many at once |
| Propose options, not one result | `propose_recipes` → `RecipeSummary` cards: 6 via the zero-LLM fast path, 4 via RecipeOptionsAgent (`proposal_count*` on `TenantConfig`) |
| Compose / extract full recipe | `get_recipe_details` → WebFetch (known URL) or WebSearch, RecipeGen fallback |
| Adapt to servings / ingredients | servings & onboarding context passed into fetch/gen prompts |
| Calendar / meal planning | `add_to_calendar` / `remove_from_calendar` |
| Structured shopping list | `get_shopping_list` over a date range → ShoppingListAgent, then (opt-in) pantry subtraction |
| General cooking Q&A | answered directly, no tool call |
| Source trust & transparency | `search_site_filter` from user prefs; `source_url` preserved on the Recipe |
| Graceful fallback | when `allow_ai_generated=False` and web search finds nothing, return a `source="not_found"` placeholder so the agent can explain and suggest changing sources / enabling AI |

## Sub-agent catalogue

| Agent | File / Factory | Output | Job |
|---|---|---|---|
| RecipeOptionsAgent | `recipe_options.py` / `build_recipe_options_agent` | `list[RecipeSummary]` (4) | Mix of web-found + AI variations (web-only when AI disabled) |
| WebSearchAgent | `web_search.py` / `build_web_search_agent` | `Recipe \| None` | DDG search → fetch → extract; never invents content |
| WebFetchAgent | `web_search.py` / `build_web_fetch_agent` | `Recipe \| None` | Fetch a known URL → extract VERBATIM (no scaling; skips re-search). Build it with `pinned_url=` — see "Fetching a known URL" below |
| RecipeGenAgent | `recipe_gen.py` / `build_recipe_gen_agent` | `Recipe` | Generate a recipe only when allowed and web search found nothing |
| RecipeScaleAgent | `recipe_scale.py` / `build_recipe_scale_agent` | `ScaledIngredients` | Scale a web recipe's quantities to the user's servings — SEPARATE from extraction |
| ShoppingListAgent | `shopping_list.py` / `build_shopping_list_agent` | `ShoppingList` | Dedup, sum quantities, group by shop section |
| ProductReRankAgent | `product_rerank.py` / `build_product_rerank_agent` | `ReRankChoice` | Pick the best delivery-shop product from a lexical shortlist, or decline (adapts the `delivery-shops` `ReRanker` seam to a PydanticAI call — see [delivery-shops/CLAUDE.md](../../../delivery-shops/CLAUDE.md)) |

`recipe_search_fast.py` is **not an agent either** — it is the zero-LLM fast path
for `propose_recipes` (STEP 47). When the user names a concrete dish with no extra
requirements ("znajdź przepis na jagodzianki") there is nothing to reason about,
so the RecipeOptionsAgent is skipped entirely: DuckDuckGo search → deterministic
URL ranking → page-`<head>` scrape → `RecipeSummary` cards, with **no model call**.
Measured live: 3.50s / 6 cards / 0 tokens, vs 11.73s / 4 cards / ~2600 tokens for
the agent path (3.35x).

Three invariants to preserve:

- **Never add an `Agent` to that module.** Asked to fill a missing cooking time a
  model will invent one — the fabrication Rule 5 exists to prevent. Cards carry
  only what the page itself supplied (`difficulty=""`, `total_time_minutes=0`,
  `key_ingredients=[]` when absent) and the frontend hides empty chips.
- **The trigger is deliberately narrow** — concrete dish AND no constraint fields
  AND no constraint keyword in the raw message. A constraint appended to a DDG
  query is a keyword, not an honoured requirement, so "jagodzianki bez cukru"
  must reach the reasoning agent. It also fails closed on an empty message.
- **Both stages are time-boxed.** DDG is a ~1.9-2.8s floor we do not control;
  enrichment is capped as a whole (`_ENRICH_TOTAL_BUDGET_SECONDS`) because six
  concurrent fetches are bounded by the slowest host — one stalling site was
  measured pushing a turn from 2.9s to 6.2s. A card without its photo still works.

The unit tier must never reach the network: `tests/test_agents/conftest.py` has an
autouse fixture that neuters the fast path unless a test stubs it explicitly.

**`models/measures.py` lives in `models/`, not here** — it is a deterministic
Polish cooking-measure converter (szklanka/łyżka/łyżeczka → ml/g) merely *exposed*
to the ShoppingListAgent as a **tool**, because the LLM got fractional-cup
arithmetic wrong ("1/3 szklanki" → 150 ml instead of 80 ml). The model normalises
amounts by CALLING this code, never by guessing. Never move this math into a prompt.

It sat in `agents/` until STEP 51, when `models/pantry_math.py` also needed it
(and its `fold_text` diacritic folding) — and `agents/__init__` imports `chat.py`,
so a model importing from `agents/` is a circular import. **The layering is
one-way: `agents/` → `models/`.** Anything pure and shared belongs in `models/`.

`models/recipe_blocks.py` (STEP 45) follows the same layering rule for the same
reason: deciding whether a second ingredient block is a sauce or a second dinner
is mechanical, so it is pure Python the ChatAgent *calls*, never prompt
instructions — a model asked to judge it will occasionally split a sauce off into
its own "recipe". See "Multi-recipe pages" below.

`models/pantry_math.py` (STEP 51) is the same idea one step further: pantry
subtraction runs as **deterministic Python after `get_shopping_list`'s agent call
returns**, never as prompt instructions. Three consequences worth preserving:

- **The ShoppingListAgent still knows nothing about a pantry** — it dedups/sums/
  sections, full stop. Subtraction is a separate pass over its output, so the
  feature adds **no model call and no tokens**.
- **Quantity present → subtract; absent → tag, never drop.** `SpizarniaItem.quantity`
  is free text and usually empty, so this is the common path, not an edge case.
  Anything ambiguous (unparseable amount, mismatched unit families) is left
  completely untouched: silently under-buying is worse than a redundant line.
- **The tag is a data field** (`ShoppingItem.pantry_note` / `ShopItem.pantryNote`),
  never a suffix on `name` — the copied list text and the Frisco product lookup
  both read `name` verbatim.

## Fetching a known URL (two failure modes, both fixed — don't regress them)

When the page is already known (a pasted link, or a proposal's `source_url`),
build the fetch agent as `build_web_fetch_agent(config, pinned_url=<url>)` and do
**not** cache it (`_cached_agent`) — a pinned agent is per-URL.

1. **Never let the model retype a URL.** Tool arguments are generated text: asked
   to fetch a long slug the model corrupts it. Observed live on the chilitonka
   curry post — `.../chlebkiem-naan/` came back as `-naaan/` from the ChatAgent
   and `-na-nan/` from the fetch sub-agent, both 404 → `None` → the user was told
   "this page has no recipe". Two defences, both needed because the URL crosses
   two LLM hops: `_url_from_user_message()` (chat.py) recovers the literal the
   user pasted from `deps.current_user_message`, and `pinned_url` makes the fetch
   tool ignore its `url` argument entirely.
2. **Strip `<script>`/`<style>` before the markdown conversion.** markdownify's
   `strip=[...]` removes the *tags* but keeps their *text*, so inline CSS/JS lands
   in the markdown and eats the `_MAX_PAGE_CONTENT` budget. That page converted to
   ~238k chars with the recipe starting at ~68.5k — past the cap, so the extractor
   saw only boilerplate. `recipe_web_fetch_tool` (web_search.py) cleans the HTML
   first, cutting it to ~82k with the ingredients at ~5.1k. Use that tool, never
   PydanticAI's `web_fetch_tool` directly, and prefer cleaning over raising the cap.

## Multi-recipe pages (STEP 45) — when one URL holds two dishes

Some pages host **two independent recipes** under one URL. The motivating case is
a chilitonka post with a curry ("Składniki dla 4 osób") and a naan bread
("Składniki na 8 porcji"): verbatim extraction correctly captures both and returns
one `Recipe` with 21 ingredients and `servings=4`, so a 4-person curry silently
buys 8 portions of bread and `servings` describes only half the card.

**This is not an extraction bug.** The page really does contain both, so the fix
is not to teach the extractor to drop one — it is to ask the user. The work splits
across three layers, and the split is the point:

| Layer | Job | Where |
|---|---|---|
| Extractor | **Reports** the blocks it saw, verbatim (Rule 5) | `Recipe.components`, filled by the fetch/search prompts |
| Pure heuristic | Decides *component vs standalone* — no LLM | `models/recipe_blocks.py` |
| ChatAgent | Decides whether to **ask**, and applies the answer | `detect_split` + the `choose_recipe_split` tool |

- **`components` is empty on a normal page**, so the common path costs nothing:
  `detect_split` is a list-length check, not a model call. Both fetch paths
  (`get_recipe_from_url` and `get_recipe_details`) run it — the question belongs to
  the *page*, not to how the user reached it.
- **The model's `components` is verified against the page, not trusted.** Live runs
  had gpt-4o-mini return `components=[]` for the curry+naan page on a good fraction
  of turns, and an empty list is indistinguishable from a genuine single-recipe
  page — so the feature silently degraded to the old merged behaviour with nothing
  to notice it. `detect_split_verified` re-fetches the page text (no LLM) and, when
  a regex finds serving headings the model didn't report, re-extracts **once** with
  those counts stated. The extra model call only happens on pages that
  demonstrably have multiple headings. Fails safe in every direction: no
  `source_url`, a failed fetch, one heading, or a retry that still reports nothing
  all fall back to today's behaviour.
- **Standalone = its own serving count, different from the main recipe's, and a
  heading that doesn't name a part of the dish** (sos/krem/marynata/polewa/farsz/…).
  "na 8 porcji" is the signal that actually distinguished the two blocks here —
  prefer it over an ingredient-count threshold. Every ambiguous case (no count, no
  anchor, empty block) folds in, which is exactly today's behaviour and therefore
  never wrong in a *new* way.
- **Ask before committing to a card.** The tools return `split_question=True` and
  emit **no** `FinalRecipeEvent` — showing the merged card and then asking would
  display the very result the step prevents.
- **The pending question is in `ChatState`** (`deps.pending_split`), not a module
  global: question and answer are two WS turns and a reconnect between them lands
  on a fresh container (Architecture Rule 3). It carries the whole extraction so
  answering never re-fetches the page.
- **Scaling is deferred to the answer**, and only the MAIN dish is scaled to the
  user's target. The user asked for "curry for 4", never for "naan for 4" —
  rescaling a side dish nobody asked about is the same silent edit being fixed.
- **Splitting keeps components with their dish**: a curry+naan+sauce page yields
  two recipes (curry incl. sauce, and naan), never three. Both keep `source_url`
  (Rule 5).
- **A pending split gets its own dynamic prompt branch, ahead of the `ob.complete`
  check.** Without it the answer turn looks like any other turn and the model
  reaches for its most familiar tool — observed live answering "rozdziel" with two
  `propose_recipes` calls that web-searched for brand-new curry and naan recipes,
  discarding the page already in `deps.pending_split`. The branch must come first
  because a pasted link leaves onboarding almost empty, so nesting it under
  `ob.complete` would skip it on exactly the common path.
- **`get_recipe_from_url` persists `servings` BEFORE the split early-return.**
  `deps.onboarding.servings` is the anchor `choose_recipe_split` scales from on a
  *later* turn, so a `return` that skips the write silently drops the count — live,
  "dodaj dla 8 osób" landed a calendar entry stamped 4. Any new early return in
  that tool has to keep the write above it (STEP 46's rule, one layer deeper).

## Servings (STEP 46 + 49) — where the target count comes from

Extraction records the page's OWN count into `Recipe.original_servings`; scaling to
the user's target is separate (Rule 5) and runs in `resolve_recipe` **and**
`get_recipe_from_url`. The subtle part is where the *target* comes from, and it
differs per path — three traps, each of which shipped as a bug:

- **`deps.onboarding.servings` is the single anchor for scaling**, so any tool that
  learns a serving count must write it back there, filling blanks only and never
  overwriting an answer guided onboarding already collected. `propose_recipes` does
  this (STEP 46: it used to accept `servings`, search with it and discard it, so
  everything but the `or 2` default silently scaled wrong).
- **A pasted link populates nothing.** Proposals are skipped and the prompt tells
  the model not to run onboarding for a link, so onboarding stayed empty and
  `scale_recipe_to_servings` never ran at all. Hence `get_recipe_from_url` takes an
  explicit `servings: int = 0` argument and persists it. Don't "simplify" it back
  to reading onboarding.
- **`add_to_calendar` trusts `deps.last_recipe`, never its own arguments.** The
  `ingredients` parameter is model-generated text, so it can carry the *pre-scale*
  amounts while `last_recipe` holds the scaled ones. When `_same_dish` confirms the
  resolved recipe is this dish, its ingredients and both counts win; otherwise the
  entry falls back to `onboarding.servings` with no source count. A portion number
  stamped on a list it doesn't describe is worse than no number.

`CalendarEntry.servings` / `.source_servings` are `None`-defaulted so pre-STEP-49
entries still parse; the three display states are defined once by
`servings_are_known` / `servings_were_scaled` in `models/calendar.py` and rendered
by `frontend/src/lib/servings.ts`.

> **Every new `CalendarEntry` field must be optional.** The calendar now lives in
> Firestore (STEP 52, `users/{uid}/calendar/entries`) rather than `localStorage`,
> but the contract is unchanged: there is no migration step, so a required field
> would make every previously-saved meal plan unparseable. All additions so far
> follow this: `meal_slot` defaults to `obiad` and `servings`/`source_servings`
> to `None`, each read defensively (`entry.mealSlot ?? DEFAULT_MEAL_SLOT`) rather
> than by rewriting stored data. `MealSlot` values are **stable English keys**
> (`sniadanie`/`lunch`/`obiad`/`kolacja`) precisely because they are persisted —
> Polish display labels live in `ui_strings.py`, so changing copy can never
> invalidate a saved plan.

## The calendar is server-owned (STEP 52) — the tool still writes nothing

`add_to_calendar` / `remove_from_calendar` **emit events and persist nothing**,
exactly as Rule 4 requires. The WS handler's `_emit_event` performs the Firestore
write in the same arm that sends the WS message. Three consequences to preserve:

- **`ChatAgentDeps` gets no Firestore handle.** `dump_chat_state` serializes deps
  fields straight into a Firestore doc, so a service object there would be a
  non-serializable field inside the snapshot contract — and the tools stay
  testable with no Firestore mock. `deps.pantry` is the same pattern: the handler
  does the I/O and passes data in.
- **`deps.calendar` is loaded ONCE at the handshake, not per turn.** The server is
  the only writer on this path, so `_emit_event` keeps that same object current in
  memory instead of costing a read per message. It is read-only to the tools.
- **A stale copy is bounded and harmless.** Another device or a REST write during
  an open chat leaves the in-memory copy behind. The chat reads the calendar only
  to avoid proposing duplicates, so the cost is at most a repeated suggestion —
  never a lost entry, since every mutation is a targeted add/remove-by-id and
  never a whole-state overwrite from deps.

## State model

- **`ChatAgentDeps`** — a dataclass, one instance per WebSocket connection.
  - `onboarding` (`OnboardingState`) **accumulates across turns** until complete.
  - `search_site_filter`, `allow_ai_generated`, `pantry`, `subtract_pantry` —
    **refreshed each turn** by the WS handler from the message payload / user's
    Firestore prefs.
  - `calendar` — loaded **once per connection** from Firestore and kept current
    in memory by `_emit_event` (STEP 52); never sent up by the client.
  - `last_recipe`, `last_proposals` — carry selection context between turns.
  - `events` (`list[TurnEvent]`) — **ordered per-turn side-effect collector,
    reset each turn** via `deps.reset_turn()`, then drained into typed WS
    messages by the handler after the turn.
- Conversation history is `message_history` (PydanticAI messages), extended
  in-place by `stream_chat_response` each turn.
- **Persistence (Architecture Rule 3):** after every turn the WS handler saves a
  `ChatState` snapshot (`dump_chat_state(deps, message_history)`) to the session
  document in Firestore and restores it on (re)connect (`restore_chat_state`),
  so a reconnect on a fresh Cloud Run instance resumes the conversation.
- **Usage guardrails:** one turn (ChatAgent run + all sub-agent calls, which
  share usage via `usage=ctx.usage`) is capped by `UsageLimits` from
  `TenantConfig.usage_request_limit` / `usage_total_tokens_limit`; per-turn
  usage is logged as `chat_turn_usage` for cost attribution.

> **Rule:** deps is connection-scoped working memory; the Firestore `ChatState`
> snapshot and the session/calendar/prefs documents are the source of truth.

## Hard rules for agent work

1. **ChatAgent orchestrates; sub-agents stay dumb.** New capability = a new
   ChatAgent tool (and maybe a new stateless sub-agent), never a sub-agent that
   calls another sub-agent.
2. **Onboarding is guided, never a form.** Do not add code that blocks tool calls
   until all 5 fields are set — the user may skip ahead, change topic, or ask for
   a substitution / shopping list / calendar action at any time.
3. **Every tool boundary is a Pydantic model** (Architecture Rule 5) — see the
   `*Result` models in `chat.py`.
4. **Side effects go through deps collectors, never direct WS sends from a tool.**
   Tools append to `deps.events`; the WS handler emits the messages in order.
5. **Source URL is sacred.** A web-sourced recipe must keep `source_url` even
   after serving adaptation. Adaptation never rewrites provenance.
   **Extraction is verbatim; scaling is separate.** The fetch/search agents copy
   quantities and the serving count exactly as the page states them — they must
   never rescale. Adjusting to the user's servings is a distinct step
   (`scale_recipe_to_servings` + RecipeScaleAgent) that runs *after* extraction,
   anchored on `Recipe.original_servings`, and only rewrites `ingredients`.
   Merging the two (asking the extractor to "adjust servings") drops ingredients
   and inflates amounts — the bug this separation fixed.
6. **AI generation is gated.** Respect `allow_ai_generated`; when off, never call
   RecipeGenAgent — fall back to the "not_found" path.
7. **Tools contain their failures.** A sub-agent exception must not crash the
   turn: catch it at the tool boundary and return a structured failure
   (`source="error"`, `error=...`) the ChatAgent can explain conversationally.
8. **Sub-agent calls pass `usage=ctx.usage`** so tokens aggregate into the
   turn's shared usage budget and limits.

## Adding a new agent

1. Create `cookbot/agents/{name}.py` with a `build_{name}_agent(config: TenantConfig) -> Agent` factory (see the factory pattern in the root CLAUDE.md).
2. Define the output model in `cookbot/models/`.
3. Wire it as a **ChatAgent tool** in `chat.py` — the ChatAgent is the orchestrator; there is no separate orchestrator class.
4. Write unit tests using `TestModel`.
5. Export from `cookbot/agents/__init__.py`.
