# TASK.md — CookBot Incremental Build Plan

> **For the agent:** Complete tasks strictly in order. Do not skip ahead.
> At every `⏸ PAUSE` marker, stop coding, summarize what was built,
> list the exact commands to run for verification, and wait for human
> confirmation before proceeding to the next step.
>
> **For the human:** Each pause is a chance to test, give feedback, and
> decide whether to adjust the plan before more code is written.

---

## How to Read This File

- `★` = MVP critical path — must be done for a working product
- `○` = Phase 2 — deferred, do not implement yet
- `⏸ PAUSE` = stop and wait for human feedback
- `[ ]` = not started  `[x]` = done  `[~]` = in progress

---

## Current Step: → STEP 12 — done, awaiting next

Update this line as you progress. When pausing, write:
`Current Step: → STEP N — waiting for feedback`

---

# PHASE 1 — FOUNDATION

---

## STEP 1 ★ — Monorepo Scaffold

**Goal:** Empty-but-valid repo structure. All `pyproject.toml` files in place.
Python imports resolve. No actual logic yet.

### Tasks

- [X] Create directory tree exactly as defined in `CLAUDE.md`
- [X] `packages/cookbot-core/pyproject.toml`
  ```toml
  [project]
  name = "cookbot-core"
  version = "0.1.0"
  requires-python = ">=3.12"
  dependencies = [
      "pydantic>=2.7",
      "pydantic-ai>=0.0.14",
      "pydantic-settings>=2.3",
      "google-cloud-firestore>=2.16",
      "asyncpg>=0.29",
      "openai>=1.35",
      "structlog>=24.2",
  ]
  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  [tool.ruff.lint]
  select = ["E", "F", "I", "UP"]
  ```
- [X] `clients/tastyhub/pyproject.toml`
  ```toml
  [project]
  name = "cookbot-tastyhub"
  version = "0.1.0"
  requires-python = ">=3.12"
  dependencies = [
      "cookbot-core",
      "fastapi>=0.115",
      "uvicorn[standard]>=0.30",
      "websockets>=12.0",
      "httpx>=0.27",
      "beautifulsoup4>=4.12",  # recipe crawler
  ]
  [tool.uv.sources]
  cookbot-core = { path = "../../packages/cookbot-core", editable = true }
  ```
- [X] All `__init__.py` files for every package/subpackage (empty for now)
- [X] `packages/cookbot-core/cookbot/exceptions.py` — define these exception classes (bodies = `pass`):
  `TenantNotFoundError`, `SessionExpiredError`, `HITLTimeoutError`,
  `RecipeSearchError`, `AgentError`
- [X] `.env.example` at repo root with all vars from CLAUDE.md
- [X] `.gitignore` (Python standard + `.env`, `__pycache__`, `.venv`, `*.pyc`)
- [X] `README.md` at repo root — one paragraph description + "See CLAUDE.md"

### Verify

```bash
cd packages/cookbot-core && uv sync && uv run python -c "import cookbot; print('core ok')"
cd ../../clients/tastyhub && uv sync && uv run python -c "import cookbot; print('import from client ok')"
```

Both commands must print without errors.

### ⏸ PAUSE 1
**Report:** List all created files and their sizes. Show the import verification output.
**Human decides:** Directory structure OK? Any renames needed before we write real code?

---

## STEP 2 ★ — Core Data Models

**Goal:** All Pydantic models that flow through the entire system.
No agent logic yet — just the data shapes.

### Tasks

- [ ] `cookbot/models/tenant.py` — `TenantConfig` dataclass:
  ```python
  @dataclass
  class TenantConfig:
      tenant_id: str
      persona: str           # "You are a helpful chef assistant at TastyHub"
      language: str          # "en"
      recipe_source_url: str # "https://tastyhub.com/sitemap-recipes.xml"
      allowed_origins: list[str]
      model: str             # "gpt-4o-mini"
      embedding_model: str   # "text-embedding-3-small"
      max_hitl_rounds: int   # 3
      feature_nutrition: bool  # Phase 2 flag, default False
  ```

- [X] `cookbot/models/recipe.py` — models:
  - `ParsedIngredients(BaseModel)` — items, must_use, dietary_hints, missing_staples
  - `Recipe(BaseModel)` — name, description, ingredients, steps,
    prep_time_minutes, cook_time_minutes, difficulty, servings, tips
  - `RecipeSource` — `Enum`: `TENANT_KB`, `AI_GENERATED`
  - `RecipeSearchResult(BaseModel)` — recipe, source, similarity_score

- [X] `cookbot/models/session.py` — models:
  - `Message(BaseModel)` — role (`user`|`assistant`), content, timestamp
  - `Session(BaseModel)` — session_id, tenant_id, messages, created_at, expires_at
  - `SessionStatus` — `Enum`: `ACTIVE`, `WAITING_HITL`, `COMPLETED`, `EXPIRED`

- [X] `cookbot/hitl/models.py` — models:
  - `HITLCheckpoint(BaseModel)` — checkpoint_id, session_id, recipe, round_number, created_at
  - `HITLResponse(BaseModel)` — approved: bool, modification: str | None
  - `HITLOutcome` — `Enum`: `APPROVED`, `MODIFIED`, `REJECTED`

- [X] `cookbot/protocols/ws_messages.py` — typed WS message models:
  - `WsMessageType` — `Enum` with all types: `MESSAGE`, `TOKEN`, `AGENT_UPDATE`,
    `HITL_CHECKPOINT`, `HITL_RESPONSE`, `FINAL_RECIPE`, `ERROR`
  - `WsInbound(BaseModel)` — type, content, approved, modification (all optional)
  - `WsOutToken(BaseModel)`, `WsOutAgentUpdate(BaseModel)`,
    `WsOutHitlCheckpoint(BaseModel)`, `WsOutFinalRecipe(BaseModel)`,
    `WsOutError(BaseModel)`
  - Helper functions: `ws_send_token()`, `ws_send_agent_update()`,
    `ws_send_hitl_checkpoint()`, `ws_send_final_recipe()`, `ws_send_error()`
    — all `async def`, take `WebSocket` + model-specific params

- [X] `clients/tastyhub/app/config/tenant.py` — `TASTYHUB_CONFIG: TenantConfig` instance
  (values from ENV via `pydantic-settings`, with sensible defaults)

- [X] `packages/cookbot-core/tests/test_models.py` — basic instantiation tests for all models

### Verify

```bash
cd packages/cookbot-core
uv run pytest tests/test_models.py -v
# All tests must pass
uv run python -c "
from cookbot.models.recipe import Recipe
from cookbot.models.session import Session, SessionStatus
from cookbot.hitl.models import HITLCheckpoint, HITLResponse
from cookbot.protocols.ws_messages import WsMessageType
print('all models importable')
"
```

### ⏸ PAUSE 2
**Report:** Show test output. Paste the full field list of `Recipe` and `TenantConfig`.
**Human decides:** Are the model shapes right? Anything missing before agents are built on top?

---

## STEP 3 ★ — Firestore Service

**Goal:** Session history and HITL state can be saved and loaded from Firestore.
All other code will depend on this.

### Tasks

- [X] `cookbot/services/firestore.py` — `FirestoreService` class:
  - `__init__(project_id, database_id, tenant_id)` — creates `AsyncClient`
  - `async save_message(session_id, message: Message) -> None`
  - `async get_messages(session_id) -> list[Message]`
  - `async save_session(session: Session) -> None`
  - `async get_session(session_id) -> Session | None`
  - `async save_hitl_checkpoint(checkpoint: HITLCheckpoint) -> None`
  - `async get_hitl_checkpoint(session_id) -> HITLCheckpoint | None`
  - `async clear_hitl_checkpoint(session_id) -> None`
  - `async expire_old_sessions(ttl_hours: int) -> int` — returns count deleted

  Collection path: `sessions/{tenant_id}/{session_id}`

- [X] `cookbot/services/__init__.py` — re-export `FirestoreService`

- [X] `packages/cookbot-core/tests/test_firestore.py`:
  - All tests use Firestore emulator (`FIRESTORE_EMULATOR_HOST` env var)
  - `pytest.mark.skipif` if emulator not available
  - Test: save + load message round-trip
  - Test: save + load HITL checkpoint, then clear
  - Test: `get_session` returns `None` for unknown session_id

- [X] `docker-compose.yml` — Firestore emulator only (no postgres in MVP):
  ```yaml
  services:
    firestore-emulator:
      image: gcr.io/google.com/cloudsdktool/cloud-sdk:emulators
      command: gcloud beta emulators firestore start --host-port=0.0.0.0:8080
      ports: ["8080:8080"]
  ```

### Verify

```bash
# Start emulator
docker-compose up -d firestore-emulator
# Windows PowerShell: $env:FIRESTORE_EMULATOR_HOST="localhost:8080"
# bash/zsh: export FIRESTORE_EMULATOR_HOST=localhost:8080

cd packages/cookbot-core
uv run pytest tests/test_firestore.py -v
```

### ⏸ PAUSE 3
**Report:** Test output. Any Firestore SDK issues?
**Human decides:** Firestore structure OK? Should TTL be handled differently?

---

## STEP 4 ★ — FastAPI Skeleton + Auth

**Goal:** The API starts, the health endpoint works, API key auth validates correctly,
sessions can be created. No WebSocket or agents yet.

### Tasks

- [X] `clients/tastyhub/app/main.py` — FastAPI app with lifespan:
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      # startup: init FirestoreService, DB pool, validate config
      yield
      # shutdown: close connections
  ```
  - Mount routers: `/health`, `/v1/sessions`, `/v1/ws`
  - Global exception handlers for `TenantNotFoundError`, `SessionExpiredError`
  - Structured logging with `structlog`

- [X] `clients/tastyhub/app/middleware/auth.py` — FastAPI dependency:
  - `get_tenant_config(x_api_key: str = Header(...)) -> TenantConfig`
  - Validates key against `API_KEY` env var
  - Raises HTTP 401 with clear message on failure
  - Returns `TASTYHUB_CONFIG`

- [X] `clients/tastyhub/app/api/sessions.py`:
  - `POST /v1/sessions` → creates session in Firestore, returns `{session_id, expires_at}`
  - Requires valid API key header
  - Session ID = `uuid4()`

- [X] `GET /health` → `{status: "ok", tenant: "tastyhub", version: "0.1.0"}`

- [X] `clients/tastyhub/app/config/settings.py` — `pydantic-settings` `Settings` class
  loading all ENV vars, validated at startup (fail fast on missing required vars)

- [X] `clients/tastyhub/tests/test_api.py`:
  - Test `/health` returns 200
  - Test `POST /v1/sessions` with valid API key returns session_id
  - Test `POST /v1/sessions` with invalid API key returns 401

### Verify

```powershell
cd clients/tastyhub

# Copy and fill env
Copy-Item ..\..\env.example .env
# (edit .env: set OPENAI_API_KEY, TENANT_ID=tastyhub, API_KEY=tk_dev_local)

docker-compose up -d firestore-emulator

# Start server in a separate terminal
uv run uvicorn app.main:app --reload --port 8000

# Health check
curl.exe -s http://localhost:8000/health | python -m json.tool

# Create session (valid key)
curl.exe -s -X POST http://localhost:8000/v1/sessions `
  -H "x-api-key: tk_dev_local" | python -m json.tool

# Reject invalid key
curl.exe -s -X POST http://localhost:8000/v1/sessions `
  -H "x-api-key: wrong" -w "\nHTTP %{http_code}\n"

# Run tests
uv run pytest tests/test_api.py -v
```

### ⏸ PAUSE 4
**Report:** Paste all curl outputs and test results.
**Human decides:** API shape correct? Session response needs more fields?
Any auth changes before WebSocket is added on top?

---

## STEP 5 ★ — WebSocket Echo + Message Protocol

**Goal:** WebSocket connection works end-to-end. Messages flow in both directions.
Frontend test harness can connect and see echoed messages. No agents yet —
this validates the transport before adding complexity.

### Tasks

- [X] `clients/tastyhub/app/api/websocket.py`:
  - `WS /v1/ws/{session_id}` endpoint
  - On connect: validate session exists in Firestore, load messages
  - Echo mode: for now, receive any message and send back
    `{"type": "token", "content": "echo: {received}"}` then
    `{"type": "final_recipe", ...placeholder...}`
  - Use typed helpers from `ws_messages.py` — never raw `websocket.send_json({})`
  - Handle disconnect gracefully (catch `WebSocketDisconnect`)

- [X] `frontend/index.html` — mock cooking website:
  - Simple HTML page that looks like a cooking website (static content)
  - Floating "Ask Chef Bot" button bottom-right
  - On click: opens chat panel (div, not iframe yet)
  - Chat panel: message input + send button + message list
  - On page load: `POST /v1/sessions` to get session_id
  - Opens WebSocket to `ws://localhost:8000/v1/ws/{session_id}`
  - Displays all incoming messages with type labels
  - Renders `final_recipe` type as a formatted recipe card (name + ingredients + steps)
  - Renders `hitl_checkpoint` type as recipe card with **Approve** / **Modify** / **Reject** buttons
  - On Approve: sends `{type: "hitl_response", approved: true}`
  - On Modify: shows text input → sends `{type: "hitl_response", approved: false, modification: "..."}`

- [X] `frontend/widget.js` — placeholder only (just logs "CookBot widget loaded"):
  ```javascript
  // Full widget.js implementation is Phase 2 (iframe + embed)
  console.log('[CookBot] widget.js loaded for tenant:', document.currentScript.dataset.tenantId);
  ```

- [X] `clients/tastyhub/tests/test_websocket.py`:
  - Test: WS connect to valid session → receives welcome token
  - Test: WS connect to unknown session_id → closes with 4004 code
  - Test: send message → receives echo response

### Verify

```powershell
# Server must be running (Step 4)

# Get a session ID
$SESSION = (curl.exe -s -X POST http://localhost:8000/v1/sessions `
  -H "x-api-key: tk_dev_local" | python -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Session: $SESSION"

# Test with wscat (install: npm install -g wscat)
npx wscat -c "ws://localhost:8000/v1/ws/$SESSION" `
  --execute '{\"type\":\"message\",\"content\":\"I have chicken and spinach\"}'
# Should receive: echo token + placeholder final_recipe

# Open browser test (double-click or:)
Start-Process frontend\index.html
# → type a message → should see echoed response in chat
```

### ⏸ PAUSE 5
**Report:** Paste wscat output. Screenshot or describe what the frontend looks like.
**Human decides:** WebSocket protocol shape OK? Frontend layout usable for testing?
HITL card design requirements? Anything to change before agents are wired in?

---

# PHASE 2 — AGENT PIPELINE

---

## STEP 6 ★ — IngredientAgent

**Goal:** First real agent. Takes user fridge text, returns structured `ParsedIngredients`.
Test it in isolation before wiring into the full pipeline.

### Tasks

- [X] `cookbot/agents/ingredient.py`:
  - `build_ingredient_agent(config: TenantConfig) -> Agent[None, ParsedIngredients]`
  - System prompt uses `config.persona` and `config.language`
  - Must handle edge cases: empty input, single ingredient, foreign language input
  - Return `missing_staples` only if highly confident (salt, oil, pepper — not specialty items)

- [X] `cookbot/agents/__init__.py` — export `build_ingredient_agent`

- [X] `packages/cookbot-core/tests/test_agents/test_ingredient.py`:
  - All tests use `pydantic_ai.models.test.TestModel` — no real API calls
  - Test: "I have chicken, spinach, garlic and I'm vegan" → items contain all three,
    dietary_hints contains "vegan"
  - Test: empty string → returns empty items list, no error
  - Test: single ingredient → works

### Verify

```bash
cd packages/cookbot-core
uv run pytest tests/test_agents/test_ingredient.py -v

# Live test (uses real OpenAI — set OPENAI_API_KEY first)
uv run python -c "
import asyncio
from cookbot.agents.ingredient import build_ingredient_agent
from cookbot.models.tenant import TenantConfig

config = TenantConfig(
    tenant_id='test',
    persona='You are a helpful chef',
    language='en',
    recipe_source_url='',
    allowed_origins=[],
    model='gpt-4o-mini',
    embedding_model='text-embedding-3-small',
    max_hitl_rounds=3,
    feature_nutrition=False,
)
agent = build_ingredient_agent(config)

async def main():
    result = await agent.run('I have leftover chicken, some wilting spinach, 3 garlic cloves, and feta. I try to eat healthy.')
    print(result.data.model_dump_json(indent=2))

asyncio.run(main())
"
```

### ⏸ PAUSE 6
**Report:** Test output + paste the live test JSON output.
**Human decides:** Are the parsed ingredients correct? Should `missing_staples` be more/less aggressive?

---

## STEP 7 ★ — WebSearchAgent

**Goal:** Before generating a recipe from scratch, search the web for an existing one.
Returns a structured `Recipe` from web results, or `None` if nothing useful is found,
causing the orchestrator to fall back to `RecipeGenAgent`.

### Tasks

- [ ] `cookbot/agents/web_search.py`:
  - `build_web_search_agent(config: TenantConfig) -> Agent[None, Recipe | None]`
  - Register PydanticAI's built-in web search tool on the agent
  - System prompt: search for a recipe matching the given ingredients, return a structured
    `Recipe` if a good match is found, or `None` if results are irrelevant/empty
  - Input prompt: `f"Find a recipe using these ingredients: {ingredients.items}. Dietary: {ingredients.dietary_hints}."`
  - Agent must fill all `Recipe` fields from the search result (name, steps, times, etc.)

- [ ] `cookbot/models/recipe.py` — add `WEB_SEARCH` to `RecipeSource` enum

- [ ] `cookbot/agents/__init__.py` — export `build_web_search_agent`

- [ ] `packages/cookbot-core/tests/test_agents/test_web_search.py`:
  - All tests use `TestModel` — no real API/network calls
  - Test: agent returns a valid `Recipe` when TestModel provides structured output
  - Test: agent returns `None`-equivalent when TestModel signals no result

### Verify

```bash
cd packages/cookbot-core
uv run pytest tests/test_agents/test_web_search.py -v

# Live test (uses real OpenAI + web search)
uv run python -c "
import asyncio, os
from cookbot.agents.web_search import build_web_search_agent
from cookbot.models.tenant import TenantConfig
from cookbot.models.recipe import ParsedIngredients

config = TenantConfig(
    tenant_id='test', persona='You are a helpful chef', language='en',
    recipe_source_url='', allowed_origins=[], model='gpt-4o-mini',
    embedding_model='text-embedding-3-small', max_hitl_rounds=3, feature_nutrition=False,
)
agent = build_web_search_agent(config)

async def main():
    result = await agent.run('Find a recipe using these ingredients: chicken, spinach, garlic. Dietary: none.')
    print(result.data.model_dump_json(indent=2) if result.data else 'No result found')

asyncio.run(main())
"
```

### ⏸ PAUSE 7
**Report:** Test output + live test result. Did the web search return a real recipe?
**Human decides:** Web search quality OK? Should the fallback threshold be tuned?

---

## STEP 8 ★ — RecipeGenAgent

**Goal:** Takes `ParsedIngredients` + `TenantConfig`, generates a structured `Recipe`.
This is the fallback when `WebSearchAgent` returns nothing.

### Tasks

- [ ] `cookbot/agents/recipe_gen.py`:
  - `build_recipe_gen_agent(config: TenantConfig) -> Agent[None, Recipe]`
  - System prompt emphasizes: practical home cooking, exact quantities, numbered steps
  - Persona from `config.persona` shapes the "voice" of the recipe
  - Input prompt format: `f"Ingredients: {ingredients.items + ingredients.missing_staples}. Dietary: {ingredients.dietary_hints}."`

- [ ] `packages/cookbot-core/tests/test_agents/test_recipe_gen.py`:
  - Test: valid ingredients → recipe has name, ≥3 steps, ≥2 ingredients
  - Test: `difficulty` is one of `Easy | Medium | Hard`
  - Test: `prep_time_minutes` > 0

### Verify

```bash
uv run pytest tests/test_agents/test_recipe_gen.py -v

# Live test
uv run python -c "
import asyncio
from cookbot.agents.recipe_gen import build_recipe_gen_agent
# (same config as Step 6)
# input: ParsedIngredients from Step 6 output
# print recipe JSON
"
```

### ⏸ PAUSE 8
**Report:** Paste live recipe JSON. Does the generated recipe look good?
**Human decides:** Recipe quality OK? Any prompt tuning needed? Should steps be more/less detailed?

---

## STEP 9 ★ — RefinementAgent

**Goal:** Takes an existing `Recipe` + a human modification request, returns updated `Recipe`.

### Tasks

- [ ] `cookbot/agents/refinement.py`:
  - `build_refinement_agent(config: TenantConfig) -> Agent[None, Recipe]`
  - Input prompt: `f"Recipe: {recipe.model_dump_json()}\nModification requested: {modification}"`
  - Must preserve recipe structure (same Pydantic fields), only change content
  - System prompt: "Apply the modification faithfully. Keep the recipe practical."

- [ ] `packages/cookbot-core/tests/test_agents/test_refinement.py`:
  - Test: "make it vegan" → ingredients list changes (no meat/dairy)
  - Test: "reduce cooking time" → cook_time_minutes decreases OR steps simplify
  - Test: result is still a valid `Recipe` (Pydantic validation passes)

### Verify

```bash
uv run pytest tests/test_agents/test_refinement.py -v
```

### ⏸ PAUSE 9
**Report:** Test output. Paste a live refinement example if possible.
**Human decides:** Modification quality satisfactory? Any prompt changes?

---

## STEP 10 ★ — HITL Gate

**Goal:** The pipeline can suspend and resume. This is the most critical piece.

### Tasks

- [ ] `cookbot/hitl/gate.py` — `HITLGate` class:
  ```python
  class HITLGate:
      def __init__(self, session_id: str, firestore: FirestoreService):
          self._checkpoint_q: asyncio.Queue[HITLCheckpoint] = asyncio.Queue(1)
          self._response_q: asyncio.Queue[HITLResponse] = asyncio.Queue(1)
          self._session_id = session_id
          self._firestore = firestore

      async def suspend(self, recipe: Recipe, round_number: int) -> HITLResponse:
          """Called by pipeline. Suspends until human responds."""
          checkpoint = HITLCheckpoint(
              checkpoint_id=str(uuid4()),
              session_id=self._session_id,
              recipe=recipe,
              round_number=round_number,
              created_at=datetime.utcnow(),
          )
          await self._firestore.save_hitl_checkpoint(checkpoint)
          await self._checkpoint_q.put(checkpoint)
          response = await asyncio.wait_for(
              self._response_q.get(),
              timeout=3600.0  # 1 hour timeout
          )
          await self._firestore.clear_hitl_checkpoint(self._session_id)
          return response

      async def get_checkpoint(self) -> HITLCheckpoint:
          """Called by WS handler. Gets checkpoint to send to human."""
          return await self._checkpoint_q.get()

      async def submit_response(self, response: HITLResponse) -> None:
          """Called by WS handler. Injects human response back into pipeline."""
          await self._response_q.put(response)
  ```

- [ ] `cookbot/hitl/persistence.py`:
  - `async restore_gate_from_firestore(session_id, firestore) -> HITLCheckpoint | None`
  - Used when WS reconnects mid-HITL (human closed browser and reopened)

- [ ] `packages/cookbot-core/tests/test_hitl/test_gate.py`:
  - Test: `suspend()` blocks until `submit_response()` is called concurrently
  - Test: response value flows correctly through the queue
  - Test: `asyncio.wait_for` raises `HITLTimeoutError` on timeout (set short timeout in test)
  - Test: `rejected` response (approved=False, modification=None) propagates correctly

### Verify

```bash
uv run pytest tests/test_hitl/ -v
# All tests must pass, including the concurrency test
```

### ⏸ PAUSE 10
**Report:** Test output. Any concerns about the asyncio.Queue approach?
**Human decides:** HITL timeout of 1 hour correct? Should rejected sessions be handled differently?

---

## STEP 11 ★ — SessionOrchestrator

**Goal:** All agents wired together with the HITL gate. The full pipeline runs as a
background task. Can be tested end-to-end without WebSocket.

### Tasks

- [ ] `cookbot/orchestrator/session.py` — `SessionOrchestrator`:
  ```python
  class SessionOrchestrator:
      def __init__(self, config: TenantConfig, firestore: FirestoreService):
          self._config = config
          self._firestore = firestore

      async def run(
          self,
          session_id: str,
          user_message: str,
          # Callbacks — WS handler passes these; CLI test passes print functions
          on_token: Callable[[str], Awaitable[None]],
          on_agent_update: Callable[[str, str], Awaitable[None]],
          on_hitl_checkpoint: Callable[[HITLCheckpoint], Awaitable[None]],
          on_hitl_response_needed: Callable[[], Awaitable[HITLResponse]],
          on_final_recipe: Callable[[Recipe, RecipeSource], Awaitable[None]],
          on_error: Callable[[str], Awaitable[None]],
      ) -> None:
  ```

  Pipeline:
  1. `IngredientAgent` → `ParsedIngredients`
  2. `WebSearchAgent` → `Recipe | None` (web search for matching recipe)
  3. If web search returned a recipe → use it; else `RecipeGenAgent` → `Recipe`
  4. HITL gate: call `on_hitl_checkpoint(checkpoint)`, then `await on_hitl_response_needed()`
  5. If approved → done
  6. If modified → `RefinementAgent`, loop back to step 4 (max `config.max_hitl_rounds`)
  7. If rejected → call `on_error("Recipe rejected by user")`
  8. Call `on_final_recipe(recipe, source)`

  Save every agent output to Firestore as an assistant message.

- [ ] `packages/cookbot-core/tests/test_orchestrator/test_session.py`:
  - Use `TestModel` for all agents
  - Test: full happy path (approve on first round)
  - Test: one modification round then approve
  - Test: reject → on_error called
  - Test: max HITL rounds reached → uses last recipe

### Verify

```bash
uv run pytest tests/test_orchestrator/ -v

# CLI test (no WebSocket, no frontend needed)
uv run python -c "
import asyncio
from cookbot.orchestrator.session import SessionOrchestrator
# wire up with print callbacks, run with 'I have eggs and cheese'
# should print: agent updates, then HITL checkpoint, then ask for approve/modify in CLI
"
```

### ⏸ PAUSE 11
**Report:** Test output + paste CLI test run showing the full pipeline.
**Human decides:** Pipeline flow correct? Agent update messages useful? Any orchestration changes?

---

## STEP 12 ★ — Full WebSocket Integration

**Goal:** Replace the echo stub from Step 5 with the real orchestrator.
End-to-end: browser types fridge contents → agents run → HITL card appears → approve → recipe.

### Tasks

- [ ] Update `clients/tastyhub/app/api/websocket.py`:
  - On message received: spawn `asyncio.create_task(orchestrator.run(...))`
  - Pass WS-backed callbacks:
    - `on_token` → `await ws_send_token(ws, content)`
    - `on_agent_update` → `await ws_send_agent_update(ws, agent, status)`
    - `on_hitl_checkpoint` → `await ws_send_hitl_checkpoint(ws, checkpoint)`
    - `on_hitl_response_needed` → `return await hitl_gate.get_checkpoint()` +
      loop waiting for next WS message of type `hitl_response`
    - `on_final_recipe` → `await ws_send_final_recipe(ws, recipe, source)`
    - `on_error` → `await ws_send_error(ws, message)`
  - Handle WS disconnect mid-pipeline gracefully (cancel task)
  - Store `HITLGate` instance in app state keyed by `session_id`

- [ ] Update `frontend/index.html` — make sure HITL card handlers send the right WS messages

- [ ] `clients/tastyhub/tests/test_websocket.py` — add:
  - Test: send fridge message → eventually receive `final_recipe` (mock agents)
  - Test: send fridge message → receive `hitl_checkpoint` → send approve → receive `final_recipe`
  - Test: send fridge message → receive `hitl_checkpoint` → send modify → receive new checkpoint → approve

### Verify

```bash
# Server running with real OPENAI_API_KEY

# Automated test (mocked agents)
uv run pytest tests/test_websocket.py -v

# Manual end-to-end test in browser
open frontend/index.html
# 1. Type: "I have chicken breast, spinach, garlic, feta cheese"
# 2. Watch agent update messages appear
# 3. Recipe card should appear with Approve/Modify/Reject buttons
# 4. Click Approve → final recipe card appears
# 5. Start new session, repeat but click Modify, type "make it under 20 minutes"
# 6. New recipe appears, approve it
```

### ⏸ PAUSE 12 ★ MAJOR CHECKPOINT
**Report:** Describe the full manual test run. Did the HITL flow work?
Screenshot or describe the frontend state at each step.
**Human decides:** This is the core product working. Is the agent quality good?
Is the HITL UX right? Any changes needed before deployment?

---

# PHASE 3 — PACKAGING & DEPLOYMENT

---

## STEP 13 ★ — Docker + Local Full Stack

**Goal:** Entire stack runs with one command. Proves the container will work on Cloud Run.

### Tasks

- [ ] `clients/tastyhub/Dockerfile`:
  ```dockerfile
  FROM python:3.12-slim
  WORKDIR /app
  RUN pip install uv
  # Copy core library first (layer caching)
  COPY packages/cookbot-core /app/packages/cookbot-core
  COPY clients/tastyhub /app/clients/tastyhub
  WORKDIR /app/clients/tastyhub
  RUN uv sync --frozen --no-dev
  EXPOSE 8080
  CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
  ```

- [ ] `docker-compose.yml` — add tastyhub service:
  ```yaml
  tastyhub-api:
    build:
      context: .
      dockerfile: clients/tastyhub/Dockerfile
    ports: ["8000:8080"]
    env_file: .env
    depends_on: [firestore-emulator]
  ```

- [ ] Update `frontend/index.html` — make API URL configurable via JS variable
  (default `http://localhost:8000`, overridable for cloud testing)

### Verify

```bash
# From repo root — single command to start everything
docker-compose up --build

# Then open frontend/index.html and run a full manual test
# Should work identically to Step 11 but running in Docker
curl http://localhost:8000/health
```

### ⏸ PAUSE 13
**Report:** Does `docker-compose up --build` work cleanly? Any image size concerns?
**Human decides:** Ready to deploy to GCP?

---

## STEP 14 ★ — Cloud Run Deployment

**Goal:** The app is live on GCP. Can be tested with the real frontend.

### Tasks

- [ ] `clients/tastyhub/cloudbuild.yaml`:
  ```yaml
  steps:
    - name: gcr.io/cloud-builders/docker
      args: ['build', '-t', 'gcr.io/$PROJECT_ID/cookbot-tastyhub:$SHORT_SHA',
             '-f', 'clients/tastyhub/Dockerfile', '.']
    - name: gcr.io/cloud-builders/docker
      args: ['push', 'gcr.io/$PROJECT_ID/cookbot-tastyhub:$SHORT_SHA']
    - name: gcr.io/google.com/cloudsdktool/cloud-sdk
      args:
        - gcloud
        - run
        - deploy
        - cookbot-tastyhub
        - --image=gcr.io/$PROJECT_ID/cookbot-tastyhub:$SHORT_SHA
        - --region=europe-west1
        - --platform=managed
        - --allow-unauthenticated
        - --labels=client_id=tastyhub,app=cookbot
        - --set-secrets=OPENAI_API_KEY=openai-key:latest
  ```

- [ ] `infrastructure/scripts/setup_gcp.sh` — one-time GCP setup:
  - Create Firestore DB
  - Create secrets in Secret Manager
  - Enable required APIs
  - Create service accounts with minimal IAM roles

- [ ] Update `frontend/index.html` with Cloud Run URL detection

- [ ] `README.md` — deployment section

### Verify

```bash
# Run setup (one-time)
bash infrastructure/scripts/setup_gcp.sh

# Deploy
gcloud builds submit --config clients/tastyhub/cloudbuild.yaml

# Test live URL
CLOUD_RUN_URL=$(gcloud run services describe cookbot-tastyhub \
  --region=europe-west1 --format='value(status.url)')
curl $CLOUD_RUN_URL/health
```

### ⏸ PAUSE 14 — FINAL MVP CHECKPOINT
**Report:** Cloud Run URL. Health check output. Full manual test on live URL.
**Human decides:** MVP complete? What are the top 3 things to improve for Phase 2?

---

# PHASE 2 — DEFERRED (do not implement until Phase 1 is complete and in production)

These are listed here so the agent knows they exist and should NOT be implemented:

- `○` Cloud SQL + pgvector schema + migration
- `○` TastyHub recipe indexer (crawl sitemap → embed → pgvector)
- `○` RecipeSearchAgent — pgvector-backed, replaces/complements WebSearchAgent
- `○` NutritionAgent
- `○` Automated nightly indexer via Cloud Scheduler
- `○` Rate limiting (Redis or Firestore counters)
- `○` Memorystore Redis (replace Firestore for HITL state)
- `○` Cloud CDN + Load Balancer for widget.js
- `○` `widget.js` as proper iframe-sandboxed embed
- `○` `infrastructure/terraform/` — full IaC
- `○` `infrastructure/scripts/new_client.sh` — scaffold script
- `○` Second client onboarding (proves library portability)
- `○` Cloud Monitoring dashboards with per-client cost views
- `○` Vertex AI Vector Search (replace pgvector at scale)

---

## Agent Behaviour Rules

1. **One step at a time.** Never implement Step N+1 while waiting for feedback on Step N.
2. **Minimal viable implementation.** Each step does exactly what its tasks say — no extras.
3. **Tests before wiring.** Write tests for a module before integrating it into the pipeline.
4. **Report clearly at every pause.** Include: files created, commands run, output observed,
   any deviations from the plan and why.
5. **Ask, don't assume.** If a task is ambiguous, ask before writing code.
6. **Never touch Phase 2 items.** If you think something from Phase 2 would help Phase 1,
   raise it at the next pause — don't implement it unilaterally.
7. **Keep cookbot-core clean.** If you feel the urge to add tastyhub-specific logic to core,
   stop and raise it at the next pause instead.
