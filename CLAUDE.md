# CLAUDE.md — CookBot SaaS

> This file is the primary context document for Claude Code.
> Read it fully before touching any file. Re-read it when switching between
> `packages/` and `clients/` work.

---

## What This Project Is

A **multi-tenant AI cooking assistant** delivered as an embeddable chat widget.
Cooking websites license the product, install a `<script>` tag, and their users
get a popup chat that finds or generates recipes based on what's in their fridge.

**Key product concept:**
- Each client (cooking website) runs as its own **Cloud Run service**
- All AI agent logic lives in **`packages/cookbot-core`** — a shared Python library
- Client apps in **`clients/{name}/`** import core and add client-specific config
- This repo is structured so `cookbot-core` can eventually be published to PyPI

---

## Repo Layout

```
cookbot/
├── packages/
│   └── cookbot-core/          # shared library — YOUR IP
│       ├── cookbot/
│       │   ├── agents/        # PydanticAI agent definitions (ChatAgent + sub-agents)
│       │   ├── hitl/          # HITL checkpoint persistence (restore on reconnect)
│       │   ├── models/        # Pydantic data models
│       │   ├── protocols/     # WebSocket message schema
│       │   └── services/      # Firestore wrapper
│       └── pyproject.toml
│
├── clients/
│   └── tastyhub/              # example client app
│       ├── app/
│       │   ├── main.py        # FastAPI entry point
│       │   ├── api/           # REST + WebSocket endpoints
│       │   ├── config/        # TastyHub TenantConfig instance
│       │   └── middleware/    # API key auth
│       ├── Dockerfile
│       ├── cloudbuild.yaml
│       └── pyproject.toml
│
├── frontend/
│   ├── index.html             # mock cooking website for testing
│   └── widget.js              # embeddable chat widget
│
├── infrastructure/
│   └── terraform/             # Phase 2 — do not touch yet
│
├── docker-compose.yml         # local dev: api + firestore emulator
├── .env.example
└── TASK.md                    # incremental build tasks — read before coding
```

---

## Non-Negotiable Architecture Rules

These are hard constraints. Never violate them, even if it seems convenient.

1. **One-way dependency only**
   ```
   clients/tastyhub  →  packages/cookbot-core  →  GCP SDKs / PydanticAI
   ```
   `cookbot-core` must NEVER import anything from `clients/`.
   If you find yourself wanting to, extract the abstraction into core instead.

2. **TenantConfig drives everything**
   Agents, prompts, language, persona — all come from `TenantConfig`.
   Nothing in `cookbot-core` has hardcoded client-specific values.

3. **All state is external**
   Cloud Run containers are stateless. Conversation history and HITL state
   go to Firestore. Never store session state in module-level variables.

4. **Async all the way down**
   Use `async def` for all I/O. Never call blocking functions inside async
   context without `asyncio.to_thread()`. This includes Firestore SDK calls.

5. **Pydantic models for every boundary**
   Every input/output to agents, every WebSocket message, every Firestore
   document must have a Pydantic model. No raw dicts crossing module boundaries.

---

## Agentic Architecture

> The product is driven by **one orchestrating ChatAgent** that owns the
> conversation and delegates narrow tasks to **stateless sub-agents** via tools.
> This replaced the original rigid 5-step pipeline (see TASK.md). Read this
> before touching anything in `cookbot/agents/`.

### The shape

```
                    ┌──────────────────────────────────────┐
   WebSocket turn → │            ChatAgent                 │ ← conversation leader
                    │  (1 instance per WS connection)      │   • intent recognition / routing
                    │  output_type=str (streamed tokens)   │   • guided onboarding (not a form)
                    │  deps=ChatAgentDeps (per-connection) │   • free-chat after first recipe
                    └──────────────┬───────────────────────┘
                                   │ calls as @agent.tool
        ┌──────────────┬──────────┼───────────────┬──────────────────┐
        ▼              ▼          ▼                ▼                  ▼
  propose_recipes  get_recipe_  add_to_calendar  get_shopping_list  update_onboarding
        │           details      remove_from_…    │                  (state only)
        ▼              ▼                           ▼
  RecipeOptions   WebSearch / WebFetch        ShoppingList
     Agent         / RecipeGen Agent             Agent
  (4 summaries)   (full Recipe extract/gen)   (dedup + sections)
```

**ChatAgent is the only stateful, conversational agent.** Every sub-agent is a
single-LLM-call, stateless function built by a `build_*_agent(config)` factory
and invoked from inside a ChatAgent tool. Sub-agents never talk to each other —
the ChatAgent coordinates them.

### Responsibilities of the ChatAgent

| Responsibility | How it's implemented |
|---|---|
| Intent recognition / routing | LLM picks which tool to call from the user's message |
| Guided (non-rigid) onboarding | `update_onboarding` tool fills 5 fields; dynamic system prompt drives the next question; user can skip/fill many at once |
| Propose options, not one result | `propose_recipes` → 4 `RecipeSummary` cards |
| Compose / extract full recipe | `get_recipe_details` → WebFetch (known URL) or WebSearch, RecipeGen fallback |
| Adapt to servings / ingredients | servings & onboarding context passed into fetch/gen prompts |
| Calendar / meal planning | `add_to_calendar` / `remove_from_calendar` |
| Structured shopping list | `get_shopping_list` over a date range → ShoppingListAgent |
| General cooking Q&A | answered directly, no tool call |
| Source trust & transparency | `search_site_filter` from user prefs; `source_url` preserved on the Recipe |
| Graceful fallback | when `allow_ai_generated=False` and web search finds nothing, return a `source="not_found"` placeholder so the agent can explain and suggest changing sources / enabling AI |

### Sub-agent catalogue

| Agent | Factory | Output | Job |
|---|---|---|---|
| RecipeOptionsAgent | `build_recipe_options_agent` | `list[RecipeSummary]` (4) | Mix of web-found + AI variations (web-only when AI disabled) |
| WebSearchAgent | `build_web_search_agent` | `Recipe \| None` | DDG search → fetch → extract; never invents content |
| WebFetchAgent | `build_web_fetch_agent` | `Recipe \| None` | Fetch a known URL → extract (skips re-search) |
| RecipeGenAgent | `build_recipe_gen_agent` | `Recipe` | Generate a recipe only when allowed and web search found nothing |
| ShoppingListAgent | `build_shopping_list_agent` | `ShoppingList` | Dedup, sum quantities, group by shop section |

### State model

- **`ChatAgentDeps`** — one instance per WebSocket connection.
  - `onboarding` (`OnboardingState`) **accumulates across turns** until complete.
  - `calendar`, `search_site_filter`, `allow_ai_generated` — **refreshed each turn**
    by the WS handler from the message payload / user's Firestore prefs.
  - `last_recipe`, `last_proposals` — carry selection context between turns.
  - `calendar_adds` / `calendar_removes` / `shopping_list_items` / `recipe_options`
    — **per-turn side-effect collectors, reset each turn** by the WS handler, then
    drained into typed WS messages after the turn.
- Conversation history is `message_history` (PydanticAI messages), extended
  in-place by `stream_chat_response` each turn.

> **Rule:** deps is connection-scoped working memory, **not** the source of truth.
> Durable state (sessions, calendar, prefs) lives in Firestore (Architecture Rule 3).

### Hard rules for agent work

1. **ChatAgent orchestrates; sub-agents stay dumb.** New capability = a new
   ChatAgent tool (and maybe a new stateless sub-agent), never a sub-agent that
   calls another sub-agent.
2. **Onboarding is guided, never a form.** Do not add code that blocks tool calls
   until all 5 fields are set — the user may skip ahead, change topic, or ask for
   a substitution / shopping list / calendar action at any time.
3. **Every tool boundary is a Pydantic model** (Architecture Rule 5) — see the
   `*Result` models in `chat.py`.
4. **Side effects go through deps collectors, never direct WS sends from a tool.**
   Tools mutate `deps.calendar_adds` etc.; the WS handler emits the messages.
5. **Source URL is sacred.** A web-sourced recipe must keep `source_url` even
   after serving adaptation. Adaptation never rewrites provenance.
6. **AI generation is gated.** Respect `allow_ai_generated`; when off, never call
   RecipeGenAgent — fall back to the "not_found" path.

> To add an agent, follow **"Adding a New Agent"** below, then wire it as a
> ChatAgent tool. (There is no separate orchestrator class — the ChatAgent *is*
> the orchestrator.)

---

## Tech Stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | Use `match` statements, `TypeAlias`, `Self` where appropriate |
| Package manager | `uv` | Use `uv run`, `uv add`, never bare `pip install` |
| Web framework | FastAPI 0.115+ | Use lifespan context managers, not `@app.on_event` |
| AI agents | PydanticAI 0.0.14+ | Typed `result_type=`, use `agent.run_stream()` for streaming |
| LLM | OpenAI `gpt-4o-mini` | Default for all agents — cost-effective, good structured output |
| Web search | PydanticAI web search tool | Recipe lookup before AI generation — MVP |
| Session store | Firestore (native async SDK) | `google-cloud-firestore` with `AsyncClient` |
| Vector search | pgvector via `asyncpg` | Phase 2 — client-specific recipe KB |
| Config | `pydantic-settings` | All config from ENV, validated at startup |
| Linting | `ruff` | Run before every commit |
| Type checking | `pyright` (strict) | All public functions must have type annotations |
| Testing | `pytest` + `pytest-asyncio` | `asyncio_mode = "auto"` in pytest.ini |

---

## Environment Variables

**Required for any client app to start:**

```bash
# LLM + web search
OPENAI_API_KEY=sk-...

# GCP
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
FIRESTORE_DATABASE=(default)          # or named DB

# Client identity
TENANT_ID=tastyhub
API_KEY=tk_live_...                   # the key embedded in widget script tag

# Optional — override defaults
LOG_LEVEL=INFO
OPENAI_MODEL=gpt-4o-mini
MAX_HITL_ROUNDS=3
SESSION_TTL_HOURS=24

# Phase 2 only — not required for MVP
# DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/cookbot
# EMBEDDING_MODEL=text-embedding-3-small
```

**Local dev:** copy `.env.example` to `.env`, fill in values, docker-compose loads it automatically.
**Cloud Run:** values come from Secret Manager via `--set-secrets` flag — never bake secrets into images.

---

## Running Locally

```bash
# 1. Start dependencies (Firestore emulator only — no postgres needed for MVP)
docker-compose up -d firestore-emulator

# 2. Install (from repo root)
cd packages/cookbot-core && uv sync
cd ../../clients/tastyhub && uv sync

# 3. Run the client app
cd clients/tastyhub
uv run uvicorn app.main:app --reload --port 8000

# 4. Open test frontend
open frontend/index.html   # or serve it: python -m http.server 3000 -d frontend/
```

**Quick health check:**
```bash
curl http://localhost:8000/health
# → {"status": "ok", "tenant": "tastyhub", "version": "0.1.0"}
```

---

## Running Tests

```bash
# From repo root
cd packages/cookbot-core
uv run pytest tests/ -v

# Client app tests
cd clients/tastyhub
uv run pytest tests/ -v

# With coverage
uv run pytest tests/ --cov=cookbot --cov-report=term-missing
```

**Test conventions:**
- Unit tests for all agents: mock the LLM using `pydantic_ai.models.test.TestModel`
- Integration tests for Firestore: use Firestore emulator (`gcloud beta emulators firestore start`)
- WebSocket tests: use `httpx` with `websockets` client or FastAPI's `TestClient`
- No real OpenAI calls in tests — always mock

---

## Code Conventions

### Agent pattern (always follow this shape)

```python
# packages/cookbot-core/cookbot/agents/my_agent.py
from pydantic_ai import Agent
from cookbot.models.recipe import MyOutputModel
from cookbot.models.tenant import TenantConfig

def build_my_agent(config: TenantConfig) -> Agent[None, MyOutputModel]:
    """Factory function — always build agents with tenant config injected."""
    return Agent(
        config.model,
        result_type=MyOutputModel,
        system_prompt=f"""
        You are {config.persona}.
        Language: {config.language}.
        ... task-specific instructions ...
        """,
    )
```

**Why factory functions instead of module-level agents:**
Agents carry system prompts that include tenant config. Module-level = single-tenant.

### WebSocket message pattern

```python
# Always use typed send helpers, never ws.send_json(raw_dict)
await ws_send_token(websocket, content="Let me check...")
await ws_send_hitl_checkpoint(websocket, recipe=recipe, round=1)
await ws_send_final_recipe(websocket, recipe=recipe, source="ai_generated")
```

### Firestore key pattern

```
sessions/{tenant_id}/{session_id}
  → messages: list[Message]
  → hitl_state: HITLCheckpoint | null
  → created_at: timestamp
  → expires_at: timestamp
```

### Error handling

```python
# Use specific exception types, never bare except
from cookbot.exceptions import (
    TenantNotFoundError,
    SessionExpiredError,
    HITLTimeoutError,
    RecipeSearchError,
)
```

---

## Adding a New Client

1. Copy `clients/tastyhub/` → `clients/{new_client}/`
2. Update `clients/{new_client}/app/config/tenant.py` with client-specific `TenantConfig`
3. Update `clients/{new_client}/Dockerfile` and `cloudbuild.yaml` with new service name
4. Deploy: `gcloud run deploy cookbot-{new_client} ...`

> Phase 2: add a client-specific recipe indexer to populate pgvector KB.

cookbot-core requires **zero changes** to add a new client.

---

## Adding a New Agent

1. Create `packages/cookbot-core/cookbot/agents/{name}.py`
2. Define output model in `packages/cookbot-core/cookbot/models/`
3. Write factory function `build_{name}_agent(config: TenantConfig) -> Agent`
4. Wire it as a **ChatAgent tool** in `cookbot/agents/chat.py` (the live pipeline).
   The ChatAgent is the orchestrator — there is no separate orchestrator class.
5. Write unit tests using `TestModel`
6. Export from `cookbot/agents/__init__.py`

---

## GCP Deployment

```bash
# Build and deploy tastyhub client
cd clients/tastyhub
gcloud builds submit --config cloudbuild.yaml

# Manual Cloud Run deploy (first time)
gcloud run deploy cookbot-tastyhub \
  --image gcr.io/$PROJECT_ID/cookbot-tastyhub:latest \
  --region europe-west1 \
  --platform managed \
  --no-allow-unauthenticated \   # widget.js adds Authorization header
  --set-secrets OPENAI_API_KEY=openai-key:latest \
  --set-env-vars TENANT_ID=tastyhub \
  --labels client_id=tastyhub,app=cookbot
```

**Important:** Always include `--labels client_id={name}` for cost attribution.

---

## What NOT to Do

- **Don't use `pip install`** — use `uv add` to modify dependencies
- **Don't hardcode tenant values in core** — use `TenantConfig`
- **Don't store session state in module globals** — use Firestore
- **Don't call `asyncio.run()` inside async code** — await everything
- **Don't use `print()` in production code** — use `structlog` logger
- **Don't commit `.env`** — it's in `.gitignore`; use `.env.example` only
- **Don't mock Firestore with unittest.mock** — use the Firestore emulator
- **Don't make real LLM calls in tests** — always use `TestModel`
- **Don't add client-specific logic to cookbot-core** — it goes in clients/
- **Don't skip type annotations** — pyright strict mode will fail CI
- **Don't build pgvector/indexer in MVP** — web search covers recipe lookup until Phase 2

---

## Useful Commands

```bash
# Format + lint (run before every commit)
uv run ruff format .
uv run ruff check . --fix
uv run pyright

# Check dependency graph (verify no circular imports)
uv run pydeps cookbot --max-bacon=3 --noshow

# Start Firestore emulator for tests
docker-compose up -d firestore-emulator
export FIRESTORE_EMULATOR_HOST=localhost:8080

# Inspect WebSocket manually
npx wscat -c ws://localhost:8000/v1/ws/test-session-id \
  -H "Authorization: Bearer tk_dev_local"
```

---

## Current Status

See `TASK.md` for the current build step and what has/hasn't been built yet.
Before writing any code, read `TASK.md` to understand which step you are on
and what the acceptance criteria are for that step.
