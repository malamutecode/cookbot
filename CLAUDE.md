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
- Client apps in **`clients/{name}/`** import core and add client-specific config + indexing
- This repo is structured so `cookbot-core` can eventually be published to PyPI

---

## Repo Layout

```
cookbot/
├── packages/
│   └── cookbot-core/          # shared library — YOUR IP
│       ├── cookbot/
│       │   ├── agents/        # PydanticAI agent definitions
│       │   ├── hitl/          # Human-in-the-Loop gate logic
│       │   ├── models/        # Pydantic data models
│       │   ├── orchestrator/  # SessionOrchestrator
│       │   ├── protocols/     # WebSocket message schema
│       │   └── services/      # Firestore, pgvector wrappers
│       └── pyproject.toml
│
├── clients/
│   └── tastyhub/              # example client app
│       ├── app/
│       │   ├── main.py        # FastAPI entry point
│       │   ├── api/           # REST + WebSocket endpoints
│       │   ├── config/        # TastyHub TenantConfig instance
│       │   ├── indexer/       # recipe crawler + embedder
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
├── docker-compose.yml         # local dev: api + postgres
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

## Tech Stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | Use `match` statements, `TypeAlias`, `Self` where appropriate |
| Package manager | `uv` | Use `uv run`, `uv add`, never bare `pip install` |
| Web framework | FastAPI 0.115+ | Use lifespan context managers, not `@app.on_event` |
| AI agents | PydanticAI 0.0.14+ | Typed `result_type=`, use `agent.run_stream()` for streaming |
| LLM | OpenAI `gpt-4o-mini` | Default for all agents — cost-effective, good structured output |
| Embeddings | OpenAI `text-embedding-3-small` | For recipe indexing |
| Session store | Firestore (native async SDK) | `google-cloud-firestore` with `AsyncClient` |
| Vector search | pgvector via `asyncpg` | Direct SQL, no ORM for vector queries |
| Config | `pydantic-settings` | All config from ENV, validated at startup |
| Linting | `ruff` | Run before every commit |
| Type checking | `pyright` (strict) | All public functions must have type annotations |
| Testing | `pytest` + `pytest-asyncio` | `asyncio_mode = "auto"` in pytest.ini |

---

## Environment Variables

**Required for any client app to start:**

```bash
# LLM
OPENAI_API_KEY=sk-...

# GCP
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
FIRESTORE_DATABASE=(default)          # or named DB

# PostgreSQL + pgvector
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/cookbot

# Client identity
TENANT_ID=tastyhub
API_KEY=tk_live_...                   # the key embedded in widget script tag

# Optional — override defaults
LOG_LEVEL=INFO
OPENAI_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small
MAX_HITL_ROUNDS=3
SESSION_TTL_HOURS=24
```

**Local dev:** copy `.env.example` to `.env`, fill in values, docker-compose loads it automatically.
**Cloud Run:** values come from Secret Manager via `--set-secrets` flag — never bake secrets into images.

---

## Running Locally

```bash
# 1. Start dependencies
docker-compose up -d postgres

# 2. Install (from repo root)
cd packages/cookbot-core && uv sync
cd ../../clients/tastyhub && uv sync

# 3. Run the client app
cd clients/tastyhub
uv run uvicorn app.main:app --reload --port 8000

# 4. Open test frontend
open frontend/index.html   # or serve it: python -m http.server 3000 -d frontend/

# 5. Run recipe indexer manually (optional, requires postgres running)
uv run python -m app.indexer.recipes
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
3. Update `clients/{new_client}/app/indexer/recipes.py` with their recipe source URL
4. Update `clients/{new_client}/Dockerfile` and `cloudbuild.yaml` with new service name
5. Deploy: `gcloud run deploy cookbot-{new_client} ...`

cookbot-core requires **zero changes** to add a new client.

---

## Adding a New Agent

1. Create `packages/cookbot-core/cookbot/agents/{name}.py`
2. Define output model in `packages/cookbot-core/cookbot/models/`
3. Write factory function `build_{name}_agent(config: TenantConfig) -> Agent`
4. Register in `SessionOrchestrator.run()` pipeline
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
gcloud beta emulators firestore start --host-port=localhost:8080
export FIRESTORE_EMULATOR_HOST=localhost:8080

# Reset local postgres
docker-compose down -v && docker-compose up -d postgres

# Inspect WebSocket manually
npx wscat -c ws://localhost:8000/v1/ws/test-session-id \
  -H "Authorization: Bearer tk_dev_local"
```

---

## Current Status

See `TASK.md` for the current build step and what has/hasn't been built yet.
Before writing any code, read `TASK.md` to understand which step you are on
and what the acceptance criteria are for that step.
