# CLAUDE.md — CookBot SaaS

> This file is the repo-wide router: architecture rules, tech stack, how to run
> and test. Read it fully before touching any file. **Module-specific detail
> lives in nested `CLAUDE.md` files that load on demand when you open that
> directory** — go to the relevant one before editing there:
>
> | When working on… | Read |
> |---|---|
> | Agents / ChatAgent / sub-agents | [`packages/cookbot-core/cookbot/agents/CLAUDE.md`](packages/cookbot-core/cookbot/agents/CLAUDE.md) |
> | Grocery / delivery-shop matching | [`packages/delivery-shops/CLAUDE.md`](packages/delivery-shops/CLAUDE.md) |
> | Client app (API, Firestore keys, WS, auth) | [`clients/tastyhub/app/CLAUDE.md`](clients/tastyhub/app/CLAUDE.md) |
> | Widget / test frontend | [`frontend/CLAUDE.md`](frontend/CLAUDE.md) |
> | Deploy / GCP | [DEPLOY.md](DEPLOY.md), [GCP_ARCHITECTURE.md](GCP_ARCHITECTURE.md) |
> | Current build step / status | [TASK.md](TASK.md) |

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
│   ├── cookbot-core/          # shared library — YOUR IP
│   │   ├── cookbot/
│   │   │   ├── agents/        # PydanticAI agents (ChatAgent + sub-agents) → agents/CLAUDE.md
│   │   │   ├── hitl/          # HITL checkpoint persistence (restore on reconnect)
│   │   │   ├── models/        # Pydantic data models
│   │   │   ├── protocols/     # WebSocket message schema
│   │   │   └── services/      # Firestore wrapper
│   │   └── pyproject.toml
│   │
│   └── delivery-shops/        # standalone grocery-matching lib → delivery-shops/CLAUDE.md
│       └── delivery_shops/    # base + matcher + models + shops/ (Frisco)
│
├── clients/
│   └── tastyhub/              # example client app → app/CLAUDE.md
│       ├── app/               # main.py, api/, config/ (TenantConfig), middleware/, indexer/
│       ├── .env               # local secrets (from .env.example; not committed)
│       ├── Dockerfile
│       ├── cloudbuild.yaml
│       └── pyproject.toml
│
├── frontend/                  # widget.js (product) + Vite/React test site → frontend/CLAUDE.md
│
├── infra/                     # deploy scripts (bash) → infra/README.md
│   ├── bootstrap.sh           # one-time GCP project setup (APIs, secrets, IAM)
│   ├── deploy-backend.sh      # Cloud Build → Artifact Registry → Cloud Run
│   ├── deploy-frontend.sh     # npm build → Firebase Hosting
│   └── deploy.env             # local deploy config (from deploy.env.example; not committed)
│
├── docs/                      # flow write-ups + requirements
├── docker-compose.yml         # local dev: firestore emulator only
├── .env.example
├── DEPLOY.md                  # deployment runbook
├── README.md
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

## Agentic Architecture (summary)

The product is driven by **one orchestrating ChatAgent** (stateful, per-WebSocket
connection, streams tokens) that delegates narrow tasks to **stateless sub-agents**
via `@agent.tool` methods. Each sub-agent is a single-LLM-call function built by a
`build_*_agent(config)` factory; sub-agents never call each other — the ChatAgent
coordinates them. This replaced the original rigid 5-step pipeline.

Key invariants (full detail, catalogue, state model, and the 8 hard rules live in
**[`packages/cookbot-core/cookbot/agents/CLAUDE.md`](packages/cookbot-core/cookbot/agents/CLAUDE.md)** — read it before touching any agent):

- **ChatAgent orchestrates; sub-agents stay dumb.** New capability = a new ChatAgent tool.
- **Onboarding is guided, never a form** — never block tool calls until 5 fields are set.
- **Extraction is verbatim; scaling is separate** (RecipeScaleAgent runs *after* extraction).
- **A page with two recipes asks the user** — the extractor only *reports* blocks; a pure heuristic classifies them and the ChatAgent asks before showing a card.
- **Source URL is sacred** — provenance survives serving adaptation.
- **AI generation is gated** by `allow_ai_generated`; respect the `not_found` fallback.
- **Tools contain their failures** — return a structured failure, never crash the turn.
- **All turn state flows through `ChatAgentDeps`**; the Firestore `ChatState` snapshot is the source of truth (Architecture Rule 3).

---

## Tech Stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | Use `match` statements, `TypeAlias`, `Self` where appropriate |
| Package manager | `uv` | Use `uv run`, `uv add`, never bare `pip install` |
| Web framework | FastAPI 0.115+ | Use lifespan context managers, not `@app.on_event` |
| AI agents | PydanticAI 1.x (`pydantic-ai-slim`) | Typed `output_type=`, `instructions=`, `agent.run_stream()` for streaming |
| LLM | OpenAI, per-agent | `TenantConfig.model_*` fields pick the model per agent (cost vs quality) |
| Web search | `duckduckgo_search_tool()` + own `recipe_web_fetch_tool()` | Recipe lookup before AI generation. Plain "przepis na X" takes a zero-LLM `DDGS()` fast path (`agents/recipe_search_fast.py`) |
| Session store | Firestore (native async SDK) | `google-cloud-firestore` with `AsyncClient` |
| Vector search | pgvector via `asyncpg` | Phase 2 — client-specific recipe KB |
| Config | `pydantic-settings` | All config from ENV, validated at startup |
| Linting | `ruff` | Run before every commit |
| Type checking | `pyright` (strict) | All public functions must have type annotations |
| Testing | `pytest` + `pytest-asyncio` | `asyncio_mode = "auto"` + the `integration` marker, both in `pyproject.toml` |

---

## Environment Variables

**Required for any client app to start:**

```bash
# LLM + web search
OPENAI_API_KEY=sk-...

# GCP
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
FIRESTORE_DATABASE=(default)          # or named DB

# Local dev only — points the SDK at the emulator instead of real GCP
FIRESTORE_EMULATOR_HOST=localhost:8080

# Client identity
TENANT_ID=tastyhub
API_KEY=tk_live_...                   # the key embedded in widget script tag

# Optional — override defaults
LOG_LEVEL=INFO
MAX_HITL_ROUNDS=3
SESSION_TTL_HOURS=24
DEV_UID=                              # dev-only: accept x-dev-uid header as identity bypass; never set in prod

# User management + per-user token quotas (STEP 42)
ADMIN_UIDS=                          # comma-separated Firebase uids seeded as admins (bootstrap)
DEFAULT_DAILY_TOKEN_LIMIT=1000000    # per-user daily token budget a new user inherits; 0 = unlimited
DEFAULT_MONTHLY_TOKEN_LIMIT=10000000 # per-user monthly token budget; 0 = unlimited
QUOTA_TIMEZONE=Europe/Warsaw         # day/month boundaries for quota resets

# Access whitelist + CORS
ALLOWED_EMAILS=                      # BOOTSTRAP whitelist: exact emails or @domains, comma-sep; EMPTY = open.
                                     # Checked after token verify (REST + WS). Since STEP 44 an existing,
                                     # non-disabled Firestore UserRecord ALSO grants access, so admin-created
                                     # accounts work without a redeploy.
ALLOWED_ORIGINS=*                    # browser origins for CORS/WebSocket; comma-sep; set to the Firebase Hosting domain(s) in prod

# Per-agent model selection (see .env.example for the rationale per agent)
MODEL_CHAT=gpt-4o-mini
MODEL_SHOPPING_LIST=gpt-4o-mini
MODEL_RECIPE_GEN=gpt-4o-mini
MODEL_WEB_SEARCH=gpt-4o-mini
MODEL_RECIPE_OPTIONS=gpt-4o-mini

# Recipe proposal counts (STEP 47 fast path)
PROPOSAL_COUNT=4          # cards from the LLM (RecipeOptionsAgent) path
PROPOSAL_COUNT_FAST=6     # cards from the zero-LLM DuckDuckGo fast path
PROPOSAL_MIN_FAST=3       # below this many usable results, fall back to the LLM path

# Frisco live search (STEP 50) — all optional, read from os.environ in shops/frisco.py.
# Defaults are baked in; set only to override. Matching goes search-API-first and
# falls back to the 50 MB feed on any failure.
# FRISCO_SEARCH_URL=https://commerce.frisco.pl/api/v1/offer/products/query
# FRISCO_SEARCH_CONCURRENCY=8        # max parallel queries per shopping list
# FRISCO_SEARCH_TIMEOUT_SECONDS=10   # per-query timeout

# Phase 2 only — not required for MVP
# DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/cookbot
# EMBEDDING_MODEL=text-embedding-3-small
```

**Local dev:** copy `.env.example` to `clients/tastyhub/.env` and fill in values —
pydantic-settings loads that file at app startup (docker-compose only runs the emulator).
**Cloud Run:** values come from Secret Manager via `--set-secrets` flag — never bake secrets into images.

---

## Running Locally

Two modes. `docker-compose up --build` runs the **whole stack** — the emulator plus
the same image Cloud Build ships to Cloud Run — which is how you catch a broken
Dockerfile locally instead of in CI (compose points the container at
`firestore-emulator:8080`, overriding the `.env` value). It has no hot reload, so
for actual development use the native loop below.

```bash
# 1. Start dependencies (Firestore emulator only — no postgres needed for MVP)
docker-compose up -d firestore-emulator

# 2. Install (from repo root)
cd packages/cookbot-core  && uv sync
cd ../delivery-shops      && uv sync
cd ../../clients/tastyhub && uv sync

# 3. Run the client app
cd clients/tastyhub
uv run uvicorn app.main:app --reload --port 8000

# 4. Run the test frontend (Vite dev server → http://localhost:3000)
cd frontend
npm install        # first time only
npm run dev
```

**Quick health check:**
```bash
curl http://localhost:8000/health
# → {"status": "ok", "tenant": "tastyhub", "version": "0.1.0"}
```

---

## Running Tests

Tests split into two tiers via the `integration` pytest marker:

- **Unit (default, fast, hermetic)** — no network, no LLM, no emulator. Mock the
  LLM with `pydantic_ai.models.test.TestModel`; mock Firestore with `AsyncMock`
  in client tests. This is what you run constantly and what CI runs.
- **Integration (`-m integration`)** — hits real external services and is
  excluded from the default run. Two kinds:
  - `tests/test_firestore.py` — needs the Firestore emulator (`FIRESTORE_EMULATOR_HOST`).
  - `tests/integration/` — **live OpenAI + DuckDuckGo** end-to-end (auto-skips
    without `OPENAI_API_KEY`; auto-loads the key from `clients/tastyhub/.env`;
    runs on `gpt-4o-mini` for TPM headroom). Costs money; occasionally flaky
    (live web search). Use to validate the real chat→proposals→recipe→calendar flow.

```bash
# Fast unit run (do this by default)
cd packages/cookbot-core && uv run pytest -m "not integration" -q
cd clients/tastyhub     && uv run pytest -q          # all client tests are unit

# Firestore integration (needs the emulator)
docker-compose up -d firestore-emulator
export FIRESTORE_EMULATOR_HOST=localhost:8080
cd packages/cookbot-core && uv run pytest -m integration tests/test_firestore.py -v

# Live LLM e2e (needs OPENAI_API_KEY; ~1 min, makes real API + web calls)
cd packages/cookbot-core && uv run pytest -m integration tests/integration/ -v

# Coverage (unit only)
uv run pytest -m "not integration" --cov=cookbot --cov-report=term-missing
```

**Test conventions:**
- Unit tests for all agents: mock the LLM using `pydantic_ai.models.test.TestModel`
- Mark anything that hits a real external service with `@pytest.mark.integration`
  (or `pytestmark = pytest.mark.integration` at module level)
- WebSocket tests: use `httpx` with `websockets` client or FastAPI's `TestClient`
- **No real OpenAI calls in the unit suite** — always mock. Live calls live only
  under `-m integration`.

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
        config.model_my_agent,        # per-agent model field on TenantConfig
        output_type=MyOutputModel,
        defer_model_check=True,
        instructions=f"""
        You are {config.persona}.
        Language: {config.language}.
        ... task-specific instructions ...
        """,
    )
```

**Why factory functions instead of module-level agents:**
Agents carry system prompts that include tenant config. Module-level = single-tenant.

### WebSocket message pattern & Firestore keys

Use the typed WS send helpers (never `ws.send_json(raw_dict)`); tools append
`TurnEvent`s to `deps.events` rather than sending directly. The full WS helper
list and the Firestore key layout live in
[`clients/tastyhub/app/CLAUDE.md`](clients/tastyhub/app/CLAUDE.md).

### Error handling

```python
# Use specific exception types, never bare except
from cookbot.exceptions import (
    TenantNotFoundError,
    SessionExpiredError,
    HITLTimeoutError,
    RecipeSearchError,
    AgentError,
)
```

---

## Adding a New Client / New Agent

- **New client** → see [`clients/tastyhub/app/CLAUDE.md`](clients/tastyhub/app/CLAUDE.md). `cookbot-core` requires **zero changes**.
- **New agent** → see [`packages/cookbot-core/cookbot/agents/CLAUDE.md`](packages/cookbot-core/cookbot/agents/CLAUDE.md). Always a factory + a ChatAgent tool + `TestModel` unit tests.
- **New delivery shop** → see [`packages/delivery-shops/CLAUDE.md`](packages/delivery-shops/CLAUDE.md).

---

## GCP Deployment

Deployment is scripted — do not hand-roll `gcloud` commands. All config comes from
`infra/deploy.env` (gitignored; copy from `deploy.env.example`). Every script takes
`--dry-run` and `--help`.

```bash
./infra/bootstrap.sh          # once per GCP project: APIs, Firestore, secrets, IAM
./infra/deploy-backend.sh     # Cloud Build → Artifact Registry → Cloud Run + /health smoke test
./infra/deploy-frontend.sh    # npm run build → Firebase Hosting
```

Backend and frontend deploy independently. Details, flags and the guardrails the
scripts enforce (service-name vs `firebase.json` rewrites, `VITE_WS_BASE` vs the
live service) are in [`infra/README.md`](infra/README.md) and [DEPLOY.md](DEPLOY.md).

**Important:** Cloud Run services carry `--labels client_id={name}` for cost
attribution — `deploy-backend.sh` sets this; preserve it in any manual override.

---

## What NOT to Do

- **Don't use `pip install`** — use `uv add` to modify dependencies
- **Don't hardcode tenant values in core** — use `TenantConfig`
- **Don't store session state in module globals** — use Firestore
- **Don't call `asyncio.run()` inside async code** — await everything
- **Don't use `print()` in production code** — use `structlog` logger
- **Don't commit `.env`** — it's in `.gitignore`; use `.env.example` only
- **Don't test FirestoreService itself with unittest.mock** — its integration tests
  run against the emulator (`tests/test_firestore.py`). Client unit tests may stub
  the *service object* with `AsyncMock` (see Running Tests).
- **Don't make real LLM calls in tests** — always use `TestModel`
- **Don't add client-specific logic to cookbot-core** — it goes in clients/
- **Don't skip type annotations** — pyright strict mode will fail CI
- **Don't build pgvector/indexer in MVP** — web search covers recipe lookup until Phase 2

---

## Useful Commands

> **Two known tooling quirks — don't chase either as a new problem.**
> Pyright's baseline is **non-zero** (57 errors: 7 delivery-shops / 19 core / 31
> client) and that is expected — all but two are loosely typed test fixtures. Judge
> a change by whether it *moves* that count: `tools/check_pyright.py` enforces
> exactly that against `tools/pyright_baseline.json`, failing on new errors *and*
> on a stale baseline after you fix some. Likewise `ruff format .` reformats ~55
> pre-existing files repo-wide (it collapses the codebase's aligned trailing-comment
> style), so never run it inside a feature commit; the gate is `ruff check`.

```bash
# Lint + types (run before every commit, per package)
uv run ruff check . --fix
uv run python ../../tools/check_pyright.py   # pyright vs the checked-in baseline
uv run pyright                                # raw run, if you want the errors themselves

# Check dependency graph (verify no circular imports)
uv run pydeps cookbot --max-bacon=3 --noshow

# Start Firestore emulator for tests
docker-compose up -d firestore-emulator
export FIRESTORE_EMULATOR_HOST=localhost:8080

# Inspect WebSocket manually — a session must exist first (created with the API key):
SESSION=$(curl -s -X POST http://localhost:8000/v1/sessions \
  -H "x-api-key: $API_KEY" | jq -r .session_id)
npx wscat -c "ws://localhost:8000/v1/ws/$SESSION"
# Authenticated user context: add -H "Authorization: Bearer <firebase-id-token>"
# or (dev bypass, needs DEV_UID set in .env) -H "x-dev-uid: $DEV_UID"
```

---

## Current Status

See `TASK.md` for the current build step and what has/hasn't been built yet.
Before writing any code, read `TASK.md` to understand which step you are on
and what the acceptance criteria are for that step.
