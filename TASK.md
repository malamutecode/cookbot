# TASK.md — CookBot Build Plan

> **What this file is:** the plan for what to build **next**. Nothing else.
>
> How the app works, why it works that way, and the rationale behind anything
> already shipped belong in the `CLAUDE.md` files — start at the root
> [CLAUDE.md](CLAUDE.md) router. When a step here is finished, fold anything worth
> keeping into the relevant `CLAUDE.md` and **delete the step from this file**.
>
> **Working convention:** complete steps in order. At a `⏸ PAUSE`, stop, summarise
> what was built, list verification commands, and wait for confirmation.

---

## Status

**Phases 1–3 are complete and the app is deployable** (Cloud Run backend +
Firebase Hosting frontend, scripted in `infra/`). Steps 1–52 are shipped; their
history is in `git log` and their behaviour is documented in the `CLAUDE.md` files.

**Current Step: STEP 53 / STEP 54** (below) — both are investigations first, not
build steps. STEP 54 is the larger measured win: extraction dominates a recipe
turn (~27s / ~10k tokens), and its retry can double that.

---

## STEP 53 — Measure whether the ChatAgent belongs on a faster model

**Status:** `○` not started. Investigation first, change second — the deliverable
is a decision backed by numbers, not a model swap.

### Why

Turn latency is dominated by a chain of serial LLM round-trips, and the ChatAgent
sits on the critical path **twice** in a recipe turn: once to route to a tool, and
again to narrate the result. Every other agent runs at most once. So the
orchestrator's per-call latency is multiplied by two in exactly the turn users
complain about.

`MODEL_CHAT` is `gpt-4o-mini` today. Routing is also the one job where a wrong
answer is maximally expensive: a mis-routed turn costs a full extra round-trip
plus a re-ask, which is the single worst latency event in the product. That makes
"cheap model for the orchestrator" a questionable default — it is worth measuring
rather than assuming in either direction.

Two prior measurements say latency work here pays off and that the fast path is
where the wins are: the zero-LLM DDG path measured 3.50s vs 11.73s for the agent
path, and the STEP 47 notes record a ~2600-token cost for that agent turn.

### What to do

1. **Get a baseline first.** `tests/test_agents/test_turn_performance.py` counts
   round-trips but deliberately does not time anything. Extend the live tier
   (`tests/integration/`) with a timing harness that records, per phase, wall-clock
   and `RunUsage` — `usage=ctx.usage` already aggregates sub-agent tokens, so the
   per-turn totals are available at the `stream_chat_response` boundary where
   `chat_turn_usage` is already logged.
2. **Vary only `model_chat`.** `TenantConfig` already has per-agent model fields,
   so this needs no code change — set the env var and re-run. Keep every other
   agent pinned so the comparison is clean.
3. **Record three numbers per model**, over the same fixed set of prompts:
   time-to-first-token, total turn wall-clock, and total tokens. TTFT matters most
   — it is what the user actually experiences. Note that the mid-turn progress
   events changed what the user *sees* during that window but not its length, so
   TTFT is still the honest number to optimise.
4. **Count re-asks / mis-routes**, not just speed. A model that is 200ms slower per
   call but never mis-routes is faster in practice. This is the number most likely
   to overturn the naive "bigger model = slower" conclusion.

### Acceptance criteria

- A repeatable timing script/test in `tests/integration/` that prints the three
  numbers per model, runnable against at least two values of `MODEL_CHAT`.
- A short written comparison (in this step, or folded into
  `packages/cookbot-core/cookbot/agents/CLAUDE.md` if it changes the guidance)
  covering: TTFT, total wall-clock, tokens, and observed mis-routes.
- An explicit decision recorded — **including "keep `gpt-4o-mini`"**, which is a
  valid and useful outcome. Do not swap the model without the numbers.
- No behaviour change in the unit tier: this step must stay a config-level
  experiment. If it turns into prompt edits, that is a different step.

### Notes / traps

- **Don't tune `MODEL_CHAT` and the prompt in the same experiment** — the ~2.6k-token
  static instruction block is a separate lever (prompt-prefix caching wants the
  static block first and volatile per-turn state last). Moving both at once makes
  the result uninterpretable.
- **The live tier is flaky by nature** (real DDG + OpenAI). Average several runs;
  a single sample proves nothing about a few-hundred-ms difference.
- **Cost is a real constraint, not a footnote** — STEP 42 meters per-user token
  budgets, so a model that is faster but meaningfully pricier per turn changes
  quota economics. Report tokens alongside latency.

---

## STEP 54 — Make the extraction retry conditional (it currently doubles the worst case)

**Status:** `○` not started. Measure first, then change — the log line is most of
the work.

### Why

Both extraction paths retry unconditionally when the model returns `None`:

- `resolve_recipe` — `for attempt in (1, 2)` (`agents/chat.py`)
- `get_recipe_from_url` — the same loop

Measured cost of ONE extraction on a *light* 11.5k-char page
(`tests/integration/test_turn_latency_live.py`, 2026-07-27):

```
fetch + extract   26.93s   9,822 tokens (2 requests)
TURN TOTAL        27.95s
```

So attempt 2 costs roughly **another ~27s and ~10k tokens** — on the single most
expensive operation in the product, and a heavy page (~82k chars post-clean) is
several times worse. That is the worst-case turn doubling.

The retry loop re-runs the **identical prompt against the identical page**. When
extraction returned `None` because the page has no recipe, or because the
ingredient list fell past `_MAX_PAGE_CONTENT`, attempt 2 fails the same way — a
full round-trip to learn nothing. It only pays when the failure was genuine model
flakiness.

Nobody currently knows which case dominates, because a `None` on attempt 1 is not
logged distinctly from a `None` on attempt 2. **That is the first thing to fix.**

### What to do

1. **Instrument before changing anything.** Log attempt number and outcome so the
   success rate of attempt 2 is countable — e.g. an `extraction_retry_outcome`
   event with `attempt`, `recovered: bool`, and the fetched content length. Ship
   this alone, read it against real traffic, and only then decide.
2. **Then make the retry conditional on a signal that it could plausibly help.**
   Candidates, cheapest first:
   - Retry only when the fetch actually returned content (an empty/truncated fetch
     will never extract on a second identical try).
   - Retry only on an exception / malformed output, not on a well-formed `None` —
     a confident `None` on a page with no recipe is a correct answer, and the
     `not_found` path already exists to handle it.
   - If content was truncated at `_MAX_PAGE_CONTENT`, that is a *different* bug
     (see the fetch-truncation notes in `agents/CLAUDE.md`) and retrying is the
     wrong response entirely.
3. **Keep both call sites in step.** `resolve_recipe` and `get_recipe_from_url`
   have the same loop for the same reason; they must not diverge.

### Acceptance criteria

- A log line that makes attempt-2 recovery rate countable, present in both paths.
- The retry no longer fires on failures it demonstrably cannot fix, with the
  chosen condition justified by the logged numbers — not by intuition.
- A unit test per path proving: a retryable failure still retries, and a
  non-retryable one does NOT (asserting the sub-agent was called once).
- No change to user-visible behaviour on success, and the `not_found` /
  `source="error"` fallbacks still contain their failures (Rule 7).

### Notes / traps

- **Do not simply delete the retry.** It was added because extraction is
  genuinely flaky; the goal is to stop paying for it when it cannot help, not to
  remove the recovery.
- **This interacts with STEP 53** — a different `MODEL_CHAT`/`model_web_search`
  changes the flake rate this step is tuning against. Do the instrumentation
  first; it is useful to both steps.
- **The `page_cache` dedup does NOT help here.** It removes a duplicate
  *download*; this is a duplicate *extraction*, which is ~25x more expensive.

---

# PHASE 4 — DEFERRED

Do not implement until Phase 3 is live in production.

- `○` GCS blob cache for the Frisco feed/index. **Largely obsoleted by STEP 50** —
  the search API removed the feed from the hot path, so this now only matters for
  the fallback path.
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

## Deferred product work (from shipped steps)

Kept because each is a real, decided-against gap — not a to-do list.

- **Editing portions after an entry is on the calendar** (8→4 in place) — needs
  `RecipeScaleAgent` re-run from a non-chat surface.
- **Anonymous / widget calendars** (STEP 52) — excluded by the login-required auth
  decision. Revisit when `widget.js` grows a calendar UI.
- **Multi-device calendar conflicts** (STEP 52) — no `updated_at` reconciliation
  or optimistic concurrency; two open tabs are last-write-wins.
- **Refreshing `deps.calendar` mid-connection** (STEP 52) — a REST write or another
  device during an open chat leaves the in-memory copy stale until reconnect.
  Bounded and harmless (costs at most a repeated suggestion); the fix, if it ever
  matters, is a re-read per turn or a cheap `updated_at` check.
- **Pruning old calendar entries** (STEP 52) — the per-user doc grows unboundedly.
  Fine for now (a year of meals is small), but it is the limit of the doc-per-user
  shape.
- **Frisco add-to-basket / logged-in cart / substitutes** — gated on the licensing
  blocker in
  [packages/delivery-shops/CLAUDE.md](packages/delivery-shops/CLAUDE.md), which
  also records the verified API facts.
- **Emailing the temp password** (admin-created users) — chosen against while no
  email sender is configured; additive when it lands.
- **Audit log of admin actions** — worth doing before this is multi-admin in prod.
- **Cascade delete of user subcollections** — `DELETE /v1/admin/users/{uid}` leaves
  `spizarnia` / `calendar` / `prefs` / usage counters as orphans. Harmless today
  but it is silent data retention after account deletion. See the docstring on
  `FirestoreService.delete_user_record`.
- **Caching DDG results per dish** — needs Firestore/GCS; belongs with the blob-cache work.
- **Streaming enrichment on the fast path** — needs a new `WsMessageType` plus
  merge-by-index in `ChatPanel`, and risks reintroducing an LLM call.
- **Structured `SpizarniaItem.quantity`** (number + unit at entry time, STEP 51) —
  would make pantry subtraction exact, but it is a data migration on every existing
  pantry plus an input-UX change. The flag-when-unknown behaviour in
  `models/pantry_math.py` exists precisely so the feature works without it.
- **Pantry as live stock** (deducting it after shopping, STEP 51) — the pantry is
  read-only by decision; `ws_send_spizarnia_offer` remains dead scaffolding.
- **Per-item "I have this" override in the shopping-list UI** — the `pantryNote`
  chip informs, but does not yet let the user resolve it in place.
