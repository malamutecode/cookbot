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

```bash
uv run ruff format .
uv run ruff check . --fix
uv run pyright
```
