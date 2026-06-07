# TASK.md — CookBot Incremental Build Plan

> **Working convention:** Complete tasks in order. At every `⏸ PAUSE` marker,
> stop, summarise what was built, list verification commands, and wait for
> confirmation before proceeding.

---

## Legend

- `★` = critical path
- `○` = Phase 4+ — deferred
- `⏸ PAUSE` = stop and wait for feedback
- `[x]` done · `[ ]` not started

---

## Current Step: → STEP 34 — ready to implement (STEP 33 done, awaiting PAUSE review)

---

# PHASE 1–2 — COMPLETED ✓

All steps below are done. Kept for reference only — do not re-implement.

- [x] STEP 1 — Monorepo scaffold
- [x] STEP 2 — Core data models
- [x] STEP 3 — Firestore service
- [x] STEP 4 — FastAPI skeleton + API key auth
- [x] STEP 5 — WebSocket echo + message protocol
- [x] STEP 6 — IngredientAgent
- [x] STEP 7 — WebSearchAgent (DuckDuckGo tool)
- [x] STEP 8 — RecipeGenAgent
- [x] STEP 9 — RefinementAgent
- [x] STEP 10 — HITL Gate (asyncio.Queue suspend/resume)
- [x] STEP 11 — SessionOrchestrator (legacy pipeline, kept for tests)
- [x] STEP 12 — Full WebSocket integration
- [x] STEP 15 — Firebase Auth (email/password, ID token verification)
- [x] STEP 16 — Spiżarnia REST API (CRUD per user)
- [x] STEP 17 — Spiżarnia toggle in chat (skip ingredient question, inject items)
- [x] STEP 18 — React/Vite SPA (login, chat panel, spizarnia panel, shopping list,
               resizable panels, calendar tab, NavBar)

**Architecture change since original plan:**
The rigid 5-question intake pipeline (IntakeAgent → IngredientAgent → WebSearchAgent)
was replaced with a single guided **ChatAgent** that:
- Collects the 5 intake fields conversationally via `update_onboarding` tool
- Calls `find_recipe` (WebSearch → RecipeGen fallback) once complete
- Supports free-chat after first recipe: add/remove calendar entries, shopping lists
- Persists `OnboardingState` and `message_history` across turns (connection-scoped deps)

See `GCP_ARCHITECTURE.md` for the current system diagram.

---

# PHASE 2b — PRODUCT IMPROVEMENTS

---

## STEP 21 ★ — Recipe detail modal in Calendar

**Goal:** Clicking a recipe name in the calendar opens a full-screen modal with
the complete recipe (ingredients, steps, tips, timing). Already partially
implemented — verify and complete.

### Current state
`CalendarPage.tsx` has a `RecipeModal` component and `detailRecipe` state.
`CalendarEntry` has an optional `recipe?: Recipe` field.
The "Add to calendar" button in ChatPanel passes the full `Recipe` object.
Agent-triggered `calendar_update` WS messages do **not** include full Recipe data.

### Tasks

- [x] Verify recipe modal opens when clicking a recipe name in CalendarPage
- [x] For agent-added entries (via `calendar_update` WS message): store the
      full recipe on the entry when `find_recipe` result is available in deps.
      Update `add_to_calendar` tool in `chat.py` to accept optional `recipe` JSON.
- [x] For manually added entries (recipe card "Dodaj do kalendarza" button):
      already passes full `Recipe` — verify modal works

### Verify

```
Manual: add a recipe via chat → go to calendar → click recipe name → modal opens
Manual: add recipe via "Dodaj do kalendarza" button → calendar → click → modal opens
```

### ⏸ PAUSE 21

---

## STEP 22 ★ — Shopping List Agent

**Goal:** Replace the current naive ingredient aggregation with a dedicated
`ShoppingListAgent` that deduplicates, sums quantities, and organises items
by shop section (produce, dairy, meat, bakery, etc.).

### Tasks

- [x] `packages/cookbot-core/cookbot/agents/shopping_list.py`:
  - `build_shopping_list_agent(config: TenantConfig) -> Agent[None, ShoppingList]`
  - Input: raw list of ingredient strings from multiple recipes
  - Output: `ShoppingList` with sections, deduplicated, quantities summed
  - Sections: warzywa/owoce, nabiał, mięso/ryby, piekarnia, suche produkty, inne
  - Sort within each section alphabetically

- [x] `packages/cookbot-core/cookbot/models/shopping.py` — new models:
  ```python
  class ShoppingItem(BaseModel):
      name: str
      quantity: str   # summed, e.g. "400g" or "3 szt."
      section: str    # "warzywa", "nabiał", etc.

  class ShoppingList(BaseModel):
      items: list[ShoppingItem]
      sections: list[str]  # ordered section names present
  ```

- [x] Wire into `get_shopping_list` tool in `chat.py`:
  collect raw ingredients, pass to `ShoppingListAgent`, return structured result

- [x] Update frontend `ShoppingList.tsx` to group and display by section

- [X] Tests: `test_shopping_list.py` — dedup, quantity sum, section assignment

### Verify

```
Manual: add 2 recipes to calendar with overlapping ingredients
→ ask "lista zakupów na ten tydzień"
→ shopping list shows sections, no duplicates, quantities summed
```

### ⏸ PAUSE 22

---

## STEP 23 ★ — Propose 4 Recipe Options

**Goal:** Instead of returning one recipe, `find_recipe` proposes 4 candidates
and the user picks one. Improves perceived quality and gives user agency.

### Tasks

- [x] `packages/cookbot-core/cookbot/agents/recipe_options.py`:
  - `build_recipe_options_agent(config: TenantConfig) -> Agent[None, list[RecipeSummary]]`
  - Returns exactly 4 `RecipeSummary` objects (name, description, difficulty,
    total_time_minutes, key_ingredients: list[str])
  - Mix: at least one from web search, rest AI-generated variations

- [x] `packages/cookbot-core/cookbot/models/recipe.py` — add:
  ```python
  class RecipeSummary(BaseModel):
      name: str
      description: str
      difficulty: str
      total_time_minutes: int
      key_ingredients: list[str]
      source: str  # "web_search" | "ai_generated"
  ```

- [x] Replace `find_recipe` tool with a two-step flow:
  1. `propose_recipes` tool → streams 4 `RecipeSummary` cards to frontend
  2. `get_recipe_details` tool → user picks one by name/number, returns full `Recipe`

- [x] New WS message type: `recipe_options` — list of 4 summaries
  Frontend renders 4 compact cards with "Wybieram" button on each

- [x] Update `ws_messages.py`: add `WsOutRecipeOptions` model + `ws_send_recipe_options()`

- [x] Update `ChatPanel.tsx`: handle `recipe_options` message type, render 4 cards,
  send `{"type": "message", "content": "wybieram 2"}` on click

### Verify

```
Manual: complete onboarding → 4 recipe cards appear → click one → full recipe shown
```

### ⏸ PAUSE 23

---

## STEP 24 ★ — Recipe Sources Tab (Trusted Websites)

**Goal:** User can configure which websites are used for recipe search.
Options: use only saved sites, use sites + full internet, or full internet only.
Default sites: kwestiasmaku.com, aniagotuje.pl.

### Tasks

- [x] `packages/cookbot-core/cookbot/models/user.py` — add:
  ```python
  class RecipeSource(BaseModel):
      url: str
      name: str        # display name, e.g. "Kwestia Smaku"
      enabled: bool = True

  class UserSearchPrefs(BaseModel):
      uid: str
      sources: list[RecipeSource] = []
      search_mode: str = "sites_and_internet"
      # "sites_only" | "sites_and_internet" | "internet_only"
  ```

- [x] `packages/cookbot-core/cookbot/services/firestore.py` — add:
  - `async get_search_prefs(uid: str) -> UserSearchPrefs`
  - `async save_search_prefs(prefs: UserSearchPrefs) -> None`
  - Default prefs created on first call with kwestiasmaku.com + aniagotuje.pl

- [x] `clients/tastyhub/app/api/search_prefs.py` — new REST router `/v1/search-prefs`:
  - `GET /v1/search-prefs` — returns user's preferences
  - `PUT /v1/search-prefs` — update search_mode and sources list
  - `POST /v1/search-prefs/sources` — add a site
  - `DELETE /v1/search-prefs/sources/{url_encoded}` — remove a site

- [x] Pass `UserSearchPrefs` into `find_recipe` / web search agent:
  - In `web_search_prompt()`: if `sites_only` or `sites_and_internet`,
    prefix search with `site:kwestiasmaku.com OR site:aniagotuje.pl`
  - If `internet_only`: no site restriction

- [x] Frontend — new **Źródła** tab (similar to Kalendarz tab layout):
  - List of saved sites with toggle (enable/disable each)
  - "Dodaj stronę" input field
  - Search mode selector: radio/toggle — Tylko zapisane / Zapisane + internet / Cały internet
  - NavBar: add "Źródła" tab between "Chat" and "Kalendarz"

- [x] If a recipe was found via web search, include the source URL in the result:
  - Add `source_url: str | None` to `Recipe` model
  - `WebSearchAgent` extracts and returns the URL of the page the recipe came from
  - Frontend recipe card shows a clickable "Źródło" link when `source_url` is set

### Verify

```
Manual: go to Źródła tab → see kwestiasmaku.com + aniagotuje.pl pre-loaded
→ add a custom site → set "Tylko zapisane"
→ start chat → recipe search should be restricted to those sites
```

### ⏸ PAUSE 24

---

## STEP 25 ★ — Chat input placeholder update

**Goal:** Update the chat input placeholder text to better reflect current capabilities.

### Tasks

- [x] `frontend/src/components/ChatPanel.tsx` line 220 — change placeholder:
  ```
  From: "Napisz wiadomość… (np. 'zrób mi pastę na jutro', 'lista zakupów na ten tydzień')"
  To:   "Napisz wiadomość… (np. 'zaproponuj mi danie na dziś', 'dodaj przepis do kalendarza na 30.05')"
  ```

### Verify

```
Manual: open chat, verify placeholder text is visible in the input field
```

### ⏸ PAUSE 25

---

## STEP 26 ★ — Chat processing indicator in NavBar

**Goal:** While the chat agent is processing (streaming a response), show a
visible spinner or animated dot in the NavBar next to the "Chat" tab label so
the user knows the bot is working even when viewing the Calendar or other pages.

### Tasks

- [x] Add `isProcessing` state to `ChatPanel` and lift it to `App.tsx` via a
      new `onProcessingChange: (v: boolean) => void` prop
- [x] Set `isProcessing = true` when a user message is sent; set to `false`
      when the stream ends (token timer fires, `calendar_update`,
      `shopping_list_update`, or `error` message received)
- [x] Pass `isProcessing` down to `NavBar` and render a small animated
      indicator (pulsing dot or spinner) next to the "Chat" label when true

### Verify

```
Manual: send a message → switch to Calendar tab immediately →
  NavBar "Chat" label shows a spinning/pulsing indicator while bot responds →
  indicator disappears when response is complete
```

### ⏸ PAUSE 26

---

## STEP 29 ★ — AI-generated recipes toggle in Źródła tab

**Goal:** User can disable AI-generated recipe proposals. When disabled, only
recipes actually found via web search are returned; AI fallback is suppressed.
The returned recipe may be adjusted for servings but the source URL always
points to the original page.

### Tasks

- [x] `packages/cookbot-core/cookbot/models/user.py` — add `allow_ai_generated: bool = True`
      field to `UserSearchPrefs`
- [x] `clients/tastyhub/app/api/search_prefs.py` — `PUT /v1/search-prefs` already handles
      the field via the existing model; no route changes needed
- [x] `clients/tastyhub/app/api/websocket.py` — pass `allow_ai_generated` flag from loaded
      prefs into `ChatAgentDeps`
- [x] `packages/cookbot-core/cookbot/agents/chat.py` — add `allow_ai_generated: bool = True`
      to `ChatAgentDeps`; in `get_recipe_details`, skip `RecipeGenAgent` fallback when flag is False
      and return a clear "not found" result instead
- [x] `packages/cookbot-core/cookbot/agents/recipe_options.py` — when `allow_ai_generated=False`
      in the prompt, instruct agent to only include proposals it found via web search
      (pad to 4 with additional web-searched variants if possible, otherwise return fewer)
- [x] `frontend/src/components/SourcesPage.tsx` — add toggle switch "Przepisy generowane przez AI"
      below the search mode selector; calls `PUT /v1/search-prefs`

### Verify

```
Manual: Źródła → disable AI → chat → propose recipes → all 4 cards should be web_search
→ pick one → full recipe extracted from page, source_url set
```

### ⏸ PAUSE 29

---

## STEP 30 ★ — Dish images in recipe proposal cards

**Goal:** Recipe proposal cards show a relevant dish photo so users can visually
compare options at a glance.

### Tasks

- [x] `packages/cookbot-core/cookbot/models/recipe.py` — add `image_url: str | None = None`
      to `RecipeSummary` and `Recipe`
- [x] `packages/cookbot-core/cookbot/agents/recipe_options.py` — added `search_images` DDG
      image tool; agent calls it per proposal to populate `image_url`
- [x] `packages/cookbot-core/cookbot/agents/web_search.py` — instruct agent to extract
      og:image from fetched page and set `image_url` on the returned `Recipe`
- [x] `frontend/src/types.ts` — add `image_url?: string | null` to `RecipeSummary` and `Recipe`
- [x] `frontend/src/components/ChatPanel.tsx` — proposal cards show 110px cover image
      (grey placeholder when absent); full recipe card shows 180px cover image
- [x] `frontend/src/components/CalendarPage.tsx` — recipe modal shows 200px cover image

### Verify

```
Manual: complete onboarding → 4 proposal cards appear → at least some show a dish photo
→ cards without image show a neutral grey placeholder
```

### ⏸ PAUSE 30

---

# PHASE 2c — AGENT ARCHITECTURE HARDENING

> Outcome of an architecture review (2026-06-07). The macro design
> (orchestrator ChatAgent → stateless tool sub-agents built by config factories)
> is already the live architecture and matches the "Agentic Architecture" section
> in CLAUDE.md — these steps don't change the shape, they remove the dead pipeline
> that contradicts it and tighten two loose spots (deps lifetime, side-effect
> ordering). STEP 31 (delete dead code) is a prerequisite for the rest.
> Ordered by risk after that: STEP 32 has actual bug potential, the rest is
> maintainability. Each step is self-contained — do them in order, PAUSE after each.
> No behaviour change to the *live* app is intended: chat/websocket tests stay green.

---

## STEP 31 ★ — Delete the dead legacy pipeline (prerequisite)

**Goal:** The pre-ChatAgent pipeline is dead code reachable only from tests and
itself. It contradicts the CLAUDE.md architecture ("wire as ChatAgent tool, not
SessionOrchestrator") and adds noise to every later step. Remove it before
refactoring so the remaining steps reason about one pipeline only.

### Verified dead (traced 2026-06-07 — no production importer)
- `packages/cookbot-core/cookbot/agents/intake.py` (`build_intake_agent`)
- `packages/cookbot-core/cookbot/agents/ingredient.py` (`build_ingredient_agent`, `intent_to_prompt`)
- `packages/cookbot-core/cookbot/agents/refinement.py` (`build_refinement_agent`) — used only by the orchestrator
- `packages/cookbot-core/cookbot/orchestrator/session.py` (`SessionOrchestrator`) — used only by its own test

### Must STAY (shared with the live ChatAgent — do NOT delete)
- `agents/web_search.py`, `agents/recipe_gen.py` — imported by `chat.py`
- `hitl/persistence.py` (`restore_checkpoint`) — still called in `websocket.py:98`
  > Note: `hitl/gate.py` (`HITLGate.suspend`) is used ONLY by the orchestrator.
  > After deleting the orchestrator, confirm nothing else imports `HITLGate`; if
  > not, `gate.py` may also be removed — but keep `persistence.py` and `models.py`.

### Tasks

- [x] Delete `agents/intake.py`, `agents/ingredient.py`, `agents/refinement.py`,
      `orchestrator/session.py` + `orchestrator/__init__.py` (whole dir removed).
- [x] Delete their tests: `tests/test_agents/test_intake.py`,
      `tests/test_agents/test_ingredient.py`, `tests/test_agents/test_refinement.py`,
      `tests/test_orchestrator/` (whole dir removed).
- [x] `agents/__init__.py` — removed the `intake` / `ingredient` / `refinement`
      imports and `__all__` entries. Kept `chat`, `web_search`, `recipe_gen` exports.
- [x] Confirmed `HITLGate` had no production importer (only orchestrator + its
      tests) → removed `hitl/gate.py` + `tests/test_hitl/test_gate.py`.
      Kept `hitl/persistence.py` (`restore_checkpoint`, used in websocket.py) + `models.py`.
- [x] Updated docs referencing deleted code:
      - `CLAUDE.md` repo-layout tree (dropped `orchestrator/` line; refined `hitl/` note)
      - `CLAUDE.md` "Adding a New Agent" + "Agentic Architecture" notes (no orchestrator class)
      - `GCP_ARCHITECTURE.md` diagram (HITLGate/RefinementAgent → checkpoint persistence)
        + monorepo tree (`orchestrator/session.py` line removed)
- [x] Verified: dead-reference grep clean; both packages import cleanly.

### Verify
```
uv run pytest -v   (both packages green — no import errors from deleted modules)
uv run ruff check . ; uv run pyright   (clean)
grep -ri "SessionOrchestrator\|build_intake\|build_ingredient\|build_refinement" \
  --include=*.py .   → no hits outside this step's deletions
```

### ⏸ PAUSE 31

---

## STEP 32 ★ — Make the per-turn reset contract structural

**Goal:** `ChatAgentDeps` currently mixes three lifetimes (connection-durable,
per-turn inputs, per-turn output collectors). The "reset these fields each turn"
rule lives only as hand-written lines in the WS handler — add a field and forget
a line and you get silent cross-turn state bleed. Make the contract live next to
the fields so it cannot drift.

### Current state
`clients/tastyhub/app/api/websocket.py:138-143` manually resets 5 fields each turn.
`packages/cookbot-core/cookbot/agents/chat.py:115-141` declares all lifetimes in
one flat `ChatAgentDeps`.

### Tasks

- [x] `packages/cookbot-core/cookbot/agents/chat.py` — added `reset_turn()` to
      `ChatAgentDeps` (clears recipe_ready_this_turn, calendar_adds, calendar_removes,
      shopping_list_items, recipe_options).
- [x] Regrouped `ChatAgentDeps` fields into three labelled lifetime sections
      (connection-durable · per-turn input · per-turn output) with a docstring
      stating the reset contract lives in `reset_turn()`.
- [x] `clients/tastyhub/app/api/websocket.py` — replaced the 5 manual reset lines
      with `deps.reset_turn()`; kept `deps.calendar = msg.calendar or CalendarState()`
      separate as a per-turn input.
- [x] Test `test_reset_turn_clears_collectors_and_preserves_durable` — asserts all
      collectors cleared and durable (onboarding/last_recipe/last_proposals) +
      per-turn-input (search_site_filter/allow_ai_generated) fields survive.

### Verify
```
uv run pytest packages/cookbot-core/tests/test_agents/test_chat.py -v
Manual: two recipe requests in one connection → second turn shows no leftover
  proposals/calendar adds from the first.
```

### ⏸ PAUSE 32

---

## STEP 33 ★ — Extract recipe-resolution logic out of the god-tool

**Goal:** `get_recipe_details` is a ~100-line tool closure containing the entire
fetch-vs-search-vs-generate-vs-fallback decision tree, testable only through the
agent. Extract it to a plain async function so the decision tree is unit-testable
directly and the tool becomes a thin wrapper.

### Current state
`packages/cookbot-core/cookbot/agents/chat.py:348-455` — all branches inline.

### Tasks

- [x] `packages/cookbot-core/cookbot/agents/chat.py` — added module-level
      `resolve_recipe(selected, choice, ob, *, config, site_filter, allow_ai_generated)`
      with the decision tree moved verbatim (no behaviour change), plus a pure
      `_select_proposal(proposals, choice)` helper.
- [x] Reduced `get_recipe_details` to ~13 lines: `_select_proposal` →
      `resolve_recipe(...)` → set `last_recipe`/`recipe_ready_this_turn`, clear
      `last_proposals`, return.
- [x] Tests in `test_chat.py`: 4 `_select_proposal` cases + 5 `resolve_recipe`
      cases (known-URL fetch, search-by-name, gen fallback, AI-disabled→not_found,
      ai_generated proposal) using patched stub sub-agent factories.

### Verify
```
uv run pytest packages/cookbot-core/tests/test_agents/test_chat.py -v
Manual: pick a web_search option → full recipe extracted, source_url preserved.
Manual: disable AI in Źródła → pick when web search yields nothing → not_found message.
```

### ⏸ PAUSE 33

---

## STEP 34 — Unify side-effect emission into an ordered event list

**Goal:** The WS handler hand-orders side-effects (recipe card before options,
dedup calendar adds, flatten shopping list) in `websocket.py:169-189`. Every new
side-effect means editing both a tool and that block. Replace the scattered
collectors with a single ordered list of typed outbound events the handler drains
in order.

### Tasks

- [ ] `packages/cookbot-core/cookbot/agents/chat.py` — define a typed union
      `TurnEvent = FinalRecipeEvent | RecipeOptionsEvent | CalendarAddEvent
      | CalendarRemoveEvent | ShoppingListEvent` (Pydantic models).
- [ ] Replace the per-turn collector fields with `events: list[TurnEvent] = []`;
      tools append events in the order they occur. `reset_turn()` clears it.
- [ ] `clients/tastyhub/app/api/websocket.py` — replace the 169-189 block with a
      single loop: `for ev in deps.events: await _emit(websocket, ev)` where
      `_emit` matches on event type. Move the recipe-before-options ordering into
      tool call order, not the handler.
- [ ] Keep calendar-add dedup (currently in handler) inside `add_to_calendar`.
- [ ] Tests: assert tool calls produce the expected ordered `events` list.

### Verify
```
uv run pytest -v   (both packages)
Manual: full flow — propose → pick → add to calendar → shopping list — all
  side-effects appear in the correct order in the UI.
```

### ⏸ PAUSE 34

---

## STEP 35 — Reduce prompt-coercion in onboarding flow

**Goal:** The dynamic system prompt uses all-caps `MANDATORY`/`MUST` pressure
(`chat.py:252-274`) to force deterministic flow that `next_missing_field()`
already computes in code. Keep the *data* in the prompt, drop the imperative
coercion, and let tool return values gate the flow. Lower risk — works today;
this is robustness across model versions.

### Tasks

- [ ] `packages/cookbot-core/cookbot/agents/chat.py` — rewrite `_onboarding_status`
      to present collected/next-field as plain context, not commands. Remove the
      "MANDATORY STEPS FOR THIS TURN" / "MUST" scaffolding.
- [ ] Verify `update_onboarding`'s return value (`complete`, `next_missing_field`)
      is sufficient signal for the model to ask the next question or call
      `propose_recipes` — strengthen the tool docstring if needed.
- [ ] Tests: `test_chat.py` — drive onboarding to completion with `TestModel`,
      assert no field is re-asked and `propose_recipes` fires when complete.

### Verify
```
uv run pytest packages/cookbot-core/tests/test_agents/test_chat.py -v
Manual: run full onboarding → no question repeated → 4 options after last answer.
Manual: skip-ahead ("zrób mi pastę dla 2 na 30 min") → fills multiple fields,
  asks only what's missing.
```

### ⏸ PAUSE 35

---

## STEP 36 — Hygiene cleanup

**Goal:** Remove the small footguns and dead-pipeline traps the review flagged.

### Tasks

- [ ] Mutable default args → `None` + coalesce:
      `chat.py:312` `propose_recipes(... dietary_hints: list[str] = [] ...)` and any
      similar. Confirm `uv run ruff check .` is clean.
- [ ] `clients/tastyhub/app/api/websocket.py:70-71` — the Bearer-verify
      `except Exception: pass` must at least `log.warning(...)` so auth failures
      aren't silent (CLAUDE.md "no bare excepts").
- [ ] Reconcile TASK.md STEP 30 vs code: `recipe_options.py` instructs the agent
      NOT to call image search ("images are loaded separately"), contradicting
      STEP 30's `search_images` tool note. Update whichever is stale so docs match code.
      (Legacy-export cleanup is no longer needed — STEP 31 deletes those modules.)

### Verify
```
uv run ruff check . ; uv run pyright   (both clean)
uv run pytest -v   (both packages green)
```

### ⏸ PAUSE 36

---

## STEP 37 — Separate unit vs integration tests

**Goal:** Today `cookbot-core/tests/test_firestore.py` (the only suite needing an
external connection — the Firestore emulator) sits alongside pure unit tests and
silently `skipif`s when the emulator is down. Make the split explicit so
`pytest -m "not integration"` runs a fast, hermetic unit suite and integration
tests run deliberately against the emulator.

### Current state (audited 2026-06-07)
- **Unit (no external connection):** all `cookbot-core/tests/test_agents/*`,
  `test_models.py`; all `clients/tastyhub/tests/*` (TestClient + mocked firestore).
- **Integration (needs `FIRESTORE_EMULATOR_HOST`):** `cookbot-core/tests/test_firestore.py`
  (8 tests, already self-skipping).

### Tasks

- [ ] Add a `pytest` marker in `pyproject.toml` (`[tool.pytest.ini_options] markers = ["integration: needs external services (Firestore emulator)"]`).
- [ ] Mark `test_firestore.py` with `pytestmark = pytest.mark.integration`
      (and/or move it to `tests/integration/`).
- [ ] Document the two run modes in CLAUDE.md "Running Tests":
      `pytest -m "not integration"` (fast/hermetic) vs `pytest -m integration`
      (requires `docker-compose up -d firestore-emulator` + `FIRESTORE_EMULATOR_HOST`).
- [ ] Confirm `pytest -m "not integration"` collects zero integration tests and
      passes with no emulator running.

### Verify
```
docker-compose up -d firestore-emulator
export FIRESTORE_EMULATOR_HOST=localhost:8080
cd packages/cookbot-core && uv run pytest -m integration -v   (8 pass against emulator)
unset FIRESTORE_EMULATOR_HOST
uv run pytest -m "not integration" -q                         (fast, all green, 0 skipped)
```

### ⏸ PAUSE 37

---

# PHASE 3 — PACKAGING & DEPLOYMENT

---

## STEP 27 ★ — Docker + Local Full Stack

**Goal:** Entire stack runs with one command. Proves the container works before Cloud Run.

### Tasks

- [ ] `clients/tastyhub/Dockerfile`:
  ```dockerfile
  FROM python:3.12-slim
  WORKDIR /app
  RUN pip install uv
  COPY packages/cookbot-core /app/packages/cookbot-core
  COPY clients/tastyhub /app/clients/tastyhub
  WORKDIR /app/clients/tastyhub
  RUN uv sync --frozen --no-dev
  EXPOSE 8080
  CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
  ```

- [ ] `docker-compose.yml` — add tastyhub service alongside firestore-emulator

- [ ] Verify `frontend/` Vite build works (`npm run build`) and static files are serveable

### Verify

```bash
docker-compose up --build
curl http://localhost:8000/health
```

### ⏸ PAUSE 27

---

## STEP 28 ★ — Cloud Run Deployment

**Goal:** App live on GCP.

### Tasks

- [ ] `clients/tastyhub/cloudbuild.yaml`
- [ ] `infrastructure/scripts/setup_gcp.sh` — one-time GCP project setup
- [ ] Secret Manager wiring for `OPENAI_API_KEY`, `API_KEY`, Firebase creds
- [ ] `README.md` — deployment section

### Verify

```bash
gcloud builds submit --config clients/tastyhub/cloudbuild.yaml
curl $(gcloud run services describe cookbot-tastyhub --region=europe-west1 --format='value(status.url)')/health
```

### ⏸ PAUSE 28 — FINAL MVP CHECKPOINT

---

# PHASE 4 — DEFERRED

Do not implement until Phase 3 is live in production.

- `○` Cloud SQL + pgvector + recipe KB
- `○` TastyHub recipe indexer (crawl → embed → pgvector)
- `○` RecipeSearchAgent (pgvector-backed)
- `○` NutritionAgent
- `○` Rate limiting (Redis / Firestore counters)
- `○` Memorystore Redis
- `○` Cloud CDN + Load Balancer for widget.js
- `○` `widget.js` iframe-sandboxed embed
- `○` Terraform IaC
- `○` `new_client.sh` scaffold
- `○` Second client onboarding
- `○` Cloud Monitoring per-client dashboards
- `○` Vertex AI Vector Search
