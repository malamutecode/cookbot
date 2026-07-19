---
name: pre-commit-check
description: Run the right tests before committing, chosen from the current diff. Inspects staged/unstaged changes and runs the matching tier — static (ruff/pyright/tsc) for docs, +unit pytest for Python, frontend checks for TS, and offers the live e2e integration tier when agent/chat code changed. Use before committing, when the user asks to "test before commit", "check my changes", "is this safe to commit", or to verify a change is green.
---

# pre-commit-check

Pick and run the **minimal sufficient** test tier for the current change, in this
order: decide tier from the diff → run fast tiers here → surface failures so they
can be fixed in-context → only delegate the slow live e2e tier to a subagent.

The goal is fast, correct feedback. Never run the expensive live-LLM tier "just to
be safe" — run it only when the diff touches agent/chat behavior, and even then
confirm with the user first (it costs money and hits the network).

## Step 1 — See what changed

```bash
git status --short
git diff --stat HEAD        # staged + unstaged vs last commit
```

Classify every changed path (ignore `.venv/`, `node_modules/`, `__pycache__/`,
`*.md` under vendored dirs). A change can hit several buckets — run every bucket
that matches.

| Changed paths | Bucket |
|---|---|
| Only `*.md` / docs / comments | **static-only** |
| `*.py` under `packages/cookbot-core/` | **core** |
| `*.py` under `packages/delivery-shops/` | **delivery-shops** |
| `*.py` under `clients/tastyhub/` | **client** |
| `frontend/**/*.ts` `*.tsx` | **frontend** |
| Anything under `cookbot/agents/`, `agents/chat.py`, or a prompt/`build_*_agent` | **agent-behavior** (adds the e2e offer) |

## Step 2 — Static tier (always, for any code change)

Ruff config lives in each package's `pyproject.toml` (`line-length=120`, lint
`E,F,I,UP`). Run format-check + lint on the affected package(s):

```bash
# per affected Python package, e.g. cookbot-core:
cd packages/cookbot-core && uv run ruff format --check . && uv run ruff check .
```

Pyright is **best-effort** (CLAUDE.md asks for strict types but it is not wired
into pyproject). Try it; if the tool isn't installed, note that and move on — do
not treat a missing pyright as a failure:

```bash
cd packages/cookbot-core && uv run pyright . 2>/dev/null || echo "pyright unavailable — skipped (not a failure)"
```

For the **frontend** bucket, static = typecheck:

```bash
cd frontend && npx tsc -b --noEmit
```

## Step 3 — Unit tier (fast, hermetic — the default gate)

Run the fast unit suite for each affected package. These are hermetic (mocked LLM
via `TestModel`, mocked Firestore) and quick — **always run them, never delegate
them.** Wrap in `timeout` (runs are fast; a hang is a tooling artifact):

```bash
# cookbot-core / delivery-shops / client — the -m filter excludes integration:
cd packages/cookbot-core && timeout 90 uv run pytest -m "not integration" -q
cd packages/delivery-shops && timeout 90 uv run pytest -q
cd clients/tastyhub      && timeout 90 uv run pytest -q     # all client tests are unit
```

Frontend unit tests:

```bash
cd frontend && npm test
```

## Step 4 — Live e2e tier (only for agent-behavior changes, only on confirm)

The integration tier hits **real OpenAI + DuckDuckGo** (and, for firestore tests,
the emulator). It is slow, costs money, and is occasionally flaky. Trigger it
**only** when the diff is in the **agent-behavior** bucket, and **ask the user
first**: "This change touches agent/chat logic — run the live e2e suite (real
OpenAI, ~1 min, costs money)? [y/N]".

If yes, **delegate it to the `e2e-runner` subagent** (Agent tool,
`subagent_type: "e2e-runner"`) so its long, chatty output stays out of this
context — you get back only pass/fail + any failure details. Do not run the live
suite inline.

The Firestore emulator tests (`tests/test_firestore.py`) need
`docker-compose up -d firestore-emulator` + `FIRESTORE_EMULATOR_HOST=localhost:8080`;
only run them if the change touches `services/` or Firestore persistence, and only
if the emulator is already up — otherwise note they were skipped.

## Step 5 — Report a verdict, don't auto-commit

Summarize as a short table: tier → ran? → pass/fail. On any failure, **show the
failing test name + traceback** and stop so it can be fixed here — do not paper
over it. Only when every run tier is green, tell the user it's safe to commit.
**Never commit or push from this skill** unless the user explicitly asks.

## Overrides

Honor explicit user intent over the heuristic:
- "static only" / "I'm in a hurry" → Steps 1–2 only.
- "run everything" / "full check" → run every tier including e2e (still confirm the cost once).
- "skip e2e" → never offer Step 4.
