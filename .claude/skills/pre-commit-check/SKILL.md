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

### Ruff — run all three packages, every time

Ruff is cheap (sub-second per package) and lint errors leak across packages, so
don't scope this to the diff — run the whole set on any Python change:

```bash
cd packages/cookbot-core   && uv run ruff check .
cd packages/delivery-shops && uv run ruff check .
cd clients/tastyhub        && uv run ruff check .
```

All three are green today; **any** finding is a real regression from this change.
Config is per package in `pyproject.toml` (`line-length=120`, lint `E,F,I,UP`).

**Do not run `ruff format --check`, and do not run `ruff format .`.** It reports
~55 pre-existing files repo-wide because `ruff format` collapses the codebase's
aligned trailing-comment style. That is a known, deliberate divergence — the lint
gate is `ruff check`. If the user explicitly asks to reformat, that is its own
commit, never folded into a feature change.

### Pyright — ratchet against the baseline

Pyright is a dev dependency in all three packages, so `uv run` finds it. The repo
carries a **known non-zero baseline** (57 errors, almost all untyped test
fixtures), so never gate on "zero errors" — gate on "no *new* errors" with the
ratchet script:

```bash
cd packages/cookbot-core   && uv run python ../../tools/check_pyright.py
cd packages/delivery-shops && uv run python ../../tools/check_pyright.py
cd clients/tastyhub        && uv run python ../../tools/check_pyright.py
```

Exit 0 = at baseline. It fails in two directions, and they mean opposite things:

- **New errors** → a real regression in this change. Fix the code; do **not** raise
  the baseline to make it pass.
- **Fewer errors than baseline** → you fixed something. Lower those entries in
  `tools/pyright_baseline.json` in the same commit; the script prints exactly which.

Run only the affected packages if you're in a hurry, but prefer all three — a core
change routinely moves the client's count.

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
