# CookBot SaaS

A multi-tenant AI cooking assistant delivered as an embeddable chat widget. Cooking
websites license the product, install a `<script>` tag, and their users get a popup
chat that finds or generates recipes based on what's in their fridge — then plans
meals on a calendar, builds a structured shopping list, and can match that list to
real products at a delivery shop (e.g. Frisco).

The AI logic lives in the shared **`packages/cookbot-core`** library; each client
(cooking website) is a thin **`clients/{name}`** FastAPI app that imports core and
adds its own config. **`packages/delivery-shops`** is a standalone library that
matches shopping-list items to delivery-shop product feeds.

See [CLAUDE.md](CLAUDE.md) for full architecture and conventions, and
[TASK.md](TASK.md) for the incremental build plan / current status.

## Repo layout

```
packages/cookbot-core/     shared AI agent library (ChatAgent + stateless sub-agents)
packages/delivery-shops/   ingredient → shop-product matching (Frisco, ...)
clients/tastyhub/          example client — FastAPI app, routers under /v1
frontend/                  Vite + React test app (mock cooking site + chat widget)
```

## Prerequisites

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/)
- Node.js 18+ and npm
- Docker (for the local Firestore emulator)

## Running locally

Two ways: **containers** (closest to production, one command) or a **native dev
loop** (hot reload, what you want while writing code).

### The whole stack in Docker

Builds and runs the same image Cloud Build ships to Cloud Run, alongside the
emulator. Needs `clients/tastyhub/.env` to exist first (step 2 below):

```bash
docker-compose up --build
curl http://localhost:8000/health
```

The container reaches the emulator at `firestore-emulator:8080` — compose
overrides `FIRESTORE_EMULATOR_HOST` for you, so the `localhost:8080` in your
`.env` stays correct for native runs. No hot reload here; use it to check the
image builds and boots, then switch to the native loop below.

### Native (hot reload)

**1. Start the Firestore emulator** (the only local dependency):

```bash
docker-compose up -d firestore-emulator
```

**2. Configure the client** — copy the example env and fill in `OPENAI_API_KEY`
(other defaults are fine for local dev):

```bash
cp .env.example clients/tastyhub/.env
```

**3. Install dependencies:**

```bash
cd packages/cookbot-core && uv sync
cd ../delivery-shops     && uv sync
cd ../../clients/tastyhub && uv sync
```

**4. Run the backend** (FastAPI on port 8000):

```bash
cd clients/tastyhub
uv run uvicorn app.main:app --reload --port 8000
```

Health check: `curl http://localhost:8000/health` → `{"status":"ok",...}`

**5. Run the frontend** (Vite dev server on port 3000, proxies the API):

```bash
cd frontend
npm install        # first time only
npm run dev
```

Open http://localhost:3000.

## Running tests

```bash
# Fast, hermetic unit tests (no network / LLM / emulator) — run these by default
cd packages/cookbot-core  && uv run pytest -m "not integration" -q
cd packages/delivery-shops && uv run pytest -m "not integration" -q
cd clients/tastyhub        && uv run pytest -q

# Integration tests (opt-in — hit real services), e.g. the live Frisco feed:
cd packages/delivery-shops && uv run pytest -m integration -v

# Firestore integration (needs the emulator running):
export FIRESTORE_EMULATOR_HOST=localhost:8080
cd packages/cookbot-core && uv run pytest -m integration tests/test_firestore.py -v
```

## Formatting & type checks (before committing)

Run per package (`packages/cookbot-core`, `packages/delivery-shops`,
`clients/tastyhub` — each carries its own ruff and pyright config):

```bash
uv run ruff check .                          # the lint gate — must be clean
uv run python ../../tools/check_pyright.py   # pyright vs the checked-in baseline
```

Two things to know before you chase a red run:

- **`ruff format` is not a gate.** Running it repo-wide reformats ~55 pre-existing
  files (it collapses the codebase's aligned trailing-comment style), so don't run
  it inside a feature commit. `ruff check` is the gate.
- **Pyright's baseline is non-zero and that is expected** — 57 errors, almost all
  in test files with deliberately loose fixtures.
  [`tools/check_pyright.py`](tools/check_pyright.py) enforces "no *new* errors"
  against [`tools/pyright_baseline.json`](tools/pyright_baseline.json) rather than
  "zero errors", so the count can only ratchet down. If you fix errors, lower the
  baseline in the same commit — the script prints exactly which entries to change.

Plain `uv run pyright` also works (pyright is a dev dependency in all three
packages); it just prints the raw baseline count instead of comparing it.

## Deploying

Deployment is scripted — don't hand-roll `gcloud`. Backend and frontend deploy
independently, and every script takes `--dry-run` and `--help`:

```bash
./infra/bootstrap.sh          # once per GCP project: APIs, Firestore, secrets, IAM
./infra/deploy-backend.sh     # Cloud Build → Artifact Registry → Cloud Run + /health smoke test
./infra/deploy-frontend.sh    # npm run build → Firebase Hosting
```

Config lives in `infra/deploy.env` (gitignored; copy from `deploy.env.example`).
See **[DEPLOY.md](DEPLOY.md)** for the runbook and **[infra/README.md](infra/README.md)**
for script flags and the guardrails they enforce.
