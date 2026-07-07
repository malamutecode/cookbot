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

## Current Step: → Phases 1–2c + Frisco + STEP 42 (user management + token quotas) COMPLETE. Next: Phase 3 deployment — STEP 27 (Docker) then STEP 28 (Cloud Run).

---

# PHASES 1–2c — COMPLETED ✓

All steps below are done and shipped. Kept as a one-line index for reference —
do not re-implement. Full historical detail lived here previously; the code and
CLAUDE.md are now the source of truth for how each works.

### Phase 1–2 — foundation
- [x] STEP 1–12 — Monorepo scaffold, core models, Firestore service, FastAPI +
      API-key auth, WebSocket + message protocol, the original agents
      (Ingredient/WebSearch/RecipeGen/Refinement), HITL gate, SessionOrchestrator
      (later deleted in STEP 31), full WebSocket integration.
- [x] STEP 15 — Firebase Auth (email/password, ID-token verification)
- [x] STEP 16 — Spiżarnia REST API (CRUD per user)
- [x] STEP 17 — Spiżarnia toggle in chat (inject pantry items)
- [x] STEP 18 — React/Vite SPA (login, chat, spizarnia, shopping list, calendar, NavBar)

**Architecture change since the original plan:** the rigid 5-question intake
pipeline was replaced with a single guided **ChatAgent** (orchestrator) that
collects intake conversationally, proposes options, resolves full recipes, and
supports free-chat (calendar, shopping lists) — see CLAUDE.md "Agentic
Architecture" and `GCP_ARCHITECTURE.md`.

### Phase 2b — product improvements
- [x] STEP 21 — Recipe detail modal in Calendar (full recipe stored on entries)
- [x] STEP 22 — ShoppingListAgent (dedup, sum quantities, group by shop section)
- [x] STEP 23 — Propose 4 recipe options → user picks one (propose_recipes /
      get_recipe_details two-step flow + `recipe_options` WS message)
- [x] STEP 24 — Recipe Sources tab (trusted sites, search modes, `source_url`)
- [x] STEP 25 — Chat input placeholder update
- [x] STEP 26 — Chat processing indicator in NavBar
- [x] STEP 29 — AI-generated-recipes toggle (`allow_ai_generated` gate)
- [x] STEP 30 — Dish images in proposal cards (superseded by STEP 38 — the DDG
      image tool was removed; images now come from og:image downstream)

### Phase 2c — agent architecture hardening
- [x] STEP 31 — Deleted the dead legacy pipeline (intake/ingredient/refinement
      agents, SessionOrchestrator, HITLGate); kept web_search/recipe_gen +
      hitl/persistence. Docs reconciled.
- [x] STEP 32 — Made the per-turn reset contract structural (`reset_turn()` +
      three labelled lifetime sections on `ChatAgentDeps`)
- [x] STEP 33 — Extracted recipe resolution into a unit-testable `resolve_recipe`
      + `_select_proposal`; `get_recipe_details` is now a thin wrapper
- [x] STEP 34 — Unified side-effects into an ordered `events: list[TurnEvent]`
      drained by the WS handler (`_emit_event` match)
- [~] STEP 35 — Reduce onboarding prompt-coercion — **SKIPPED** (2026-06-07):
      works reliably on the current model; robustness-only refactor with real
      regression risk and hard to verify without live runs. Revisit only if
      onboarding misbehaves on a future model.
- [x] STEP 36 — Hygiene cleanup (mutable-default args, WS token-verify logging)
- [x] STEP 37 — Split unit vs integration tests (`integration` marker; unit run
      is hermetic, 0 skipped)
- [x] STEP 38 — Proposal card images (concurrent og:image fetch) + source link
- [x] STEP 39 — Web recipe extraction reliability — root cause was **bad URL
      picks** (news/article pages), not broken extraction; tightened URL
      selection to prefer single-recipe pages + honest fallback message
- [x] STEP 41 — "Znajdź w Frisco" delivery-shop product matching: standalone
      `packages/delivery-shops/` (generic `ProductMatcher` + `FriscoShop`), thin
      `POST /v1/grocery/{shop}/match` client route, `FriscoPanel.tsx`, LLM re-rank
      of the lexical shortlist. **Deferred within this feature:** GCS blob cache
      for the ~50 MB feed/index (each Cloud Run instance re-downloads + rebuilds
      on cold start today — moved to Phase 4 "deferred").

> **Note on models:** the recipe agents run on `gpt-4o-mini`, which works
> reliably and is not deprecated. The former STEP 40 (forced migration off the
> gpt-4o family before a retirement deadline) was **removed** — there is no
> deadline. Model choice is a per-agent `TenantConfig.model_*` field; swap it if
> and when a better/cheaper model warrants it, not on a schedule.

---

# PHASE 3 — PACKAGING & DEPLOYMENT

> **Ordering:** STEP 42 (user management + token quotas) must land **before**
> STEP 27/28 deployment — the app should not go live without per-user usage
> limits in place, otherwise a single user (or a runaway loop) can burn the
> OpenAI budget uncapped. Do 42 first, then package (27) and deploy (28).

---

## STEP 42 ★ — User management + per-user token quotas

**Goal:** An admin can manage users and assign a **daily** and **monthly** token
budget per user (admins included). Every chat turn's token spend is metered
against the user's remaining budget; when a budget is exhausted the chat is
refused with a clear message until it resets. This is the pre-deployment cost
guardrail — today the only limit is per-turn (`UsageLimits` from
`TenantConfig.usage_request_limit` / `usage_total_tokens_limit`), which caps one
turn but nothing stops unlimited turns.

### Current state (audited 2026-07-07)
- Per-turn usage is already computed and logged as `chat_turn_usage` in
  `packages/cookbot-core/cookbot/agents/chat.py` (input/output tokens, requests).
  Nothing persists or accumulates it per user.
- User identity is a Firebase `uid` resolved in
  `clients/tastyhub/app/middleware/auth.py` (Bearer ID token, or the `x-dev-uid`
  dev bypass gated by `DEV_UID`). There is **no role/admin concept** and no
  user-record collection — users exist only implicitly via their `uid`.
- Per-user documents already live under `users/{uid}/…` (spizarnia, prefs) — the
  quota + role records fit the same pattern.

### Design decisions (settled during build 2026-07-07)
- **Metering unit:** total tokens (input + output) per turn, taken from the
  turn's `result.usage.total_tokens` (same figure logged as `chat_turn_usage`),
  surfaced on `ChatAgentDeps.last_turn_total_tokens`.
- **`0 = unlimited`** (documented on `TokenQuota`): an admin sets a positive
  number to restrict a user; the default (0/0) means no cap.
- **Reset windows:** daily resets at local 00:00, monthly on the 1st, in
  `TenantConfig.quota_timezone` (Europe/Warsaw). Counters are keyed by period
  (`users/{uid}/usage/{2026-07-07}` and `/{2026-07}`); a new key is simply a new
  doc, so `add_usage`'s `Increment` starts from 0 — lazy reset by construction,
  no cron. Reads apply `counter_for()` to zero a stale-key counter defensively.
- **Enforcement point:** `_check_quota` runs **before** the turn (refuse the next
  turn once over budget, or if `disabled`); `_record_usage` runs **after** the
  stream completes. A turn in flight still finishes under per-turn `UsageLimits`.
  Refusal is a typed `quota_exceeded` WS message (window + localized text +
  `resets_at`), not a crash.
- **Admin auth:** `role=="admin"` on the user record; `require_admin` gates the
  admin API on the caller's own record. First admin bootstrapped via `ADMIN_UIDS`.

### Tasks

- [x] **Core models** — `models/user.py`: `TokenQuota` (0 ⇒ unlimited),
  `UserRecord` (uid/email/role/quota/disabled + `is_admin`), `UsageCounter`.
- [x] **Pure quota math** — new `models/quota.py`: `day_key`/`month_key`,
  `next_reset`, `counter_for` (lazy reset), `check_budget` → `BudgetStatus`
  (daily reason wins when both exceeded). Kept I/O-free so it's unit-testable.
- [x] **Firestore service** — `services/firestore.py`: `get_user_record`
  (create-default + `ADMIN_UIDS` seed), `save_user_record`, `list_user_records`,
  `get_usage_counter`, `add_usage` (atomic `Increment` per period key).
  **Deviation from the original sketch:** the record is stored ON the parent
  `users/{uid}` doc (under a `record` map), not `users/{uid}/meta/record` — a
  subcollection-only write leaves the parent non-existent and it would be skipped
  by the `list_user_records` collection stream. Budget-checking is the pure
  `check_budget`, not a service method.
- [x] **TenantConfig defaults** — `models/tenant.py`: `default_daily_token_limit`
  / `default_monthly_token_limit` / `quota_timezone` / `admin_uids` +
  `default_quota()`. Wired from settings in `clients/tastyhub/app/config/tenant.py`.
- [x] **Enforcement in the WS turn** — `app/api/websocket.py`: `_check_quota`
  (disabled → `error`; over-budget → `quota_exceeded`, skip turn) before the
  stream; `_record_usage(deps.last_turn_total_tokens)` after it (best-effort).
  `stream_chat_response` now sets `last_turn_total_tokens`; `reset_turn` clears it.
- [x] **Admin REST API** — `app/api/admin.py`, `/v1/admin/*` behind `require_admin`
  + `/v1/me` self-view; `GET /admin/users` (records + current-period usage),
  `PUT .../quota|role|disabled`. Mounted in `main.py`.
- [x] **Env / config** — `ADMIN_UIDS`, `DEFAULT_DAILY_TOKEN_LIMIT`,
  `DEFAULT_MONTHLY_TOKEN_LIMIT`, `QUOTA_TIMEZONE` in `settings.py` + `.env.example`;
  documented in CLAUDE.md "Environment Variables". (Also removed the stale STEP 40
  note from settings.py — gpt-4o-mini is not deprecated.)
- [x] **Frontend admin view** — `AdminPage.tsx` (user table: role, daily/monthly
  limit inline-editable, used today/this month, admin toggle, disable/enable),
  `Admin` NavBar tab shown only when `/v1/me` reports `is_admin`; `quota_exceeded`
  handled in `ChatPanel.tsx` (shows the server's localized message, keeps input
  enabled). Localized quota strings added to `ui_strings.py`.
- [x] **Tests:**
  - Core unit `test_quota.py` (14): period keys, lazy reset, budget math,
    reset-time, default-quota inheritance.
  - Core integration `test_firestore.py` (4 new): default-record creation,
    `ADMIN_UIDS` seeding, usage increment + lazy reset, `list_user_records` —
    against the **emulator** (per STEP 37 split).
  - Client unit `test_admin.py` (8): `require_admin` 403, admin list, `/me`,
    quota/role/disabled updates, auth required. `test_websocket.py` (2 new):
    over-budget refusal (no usage recorded) + usage recorded after a normal turn.

### Verify — DONE (2026-07-07)
```
core unit:      171 passed, 22 deselected      (incl. 14 quota unit tests)
client unit:    49 passed                      (incl. 8 admin + 2 WS-quota tests)
firestore int:  12 passed vs emulator          (incl. 4 quota/usage tests)
ruff: clean (both)   pyright: clean on all changed files
                     (pre-existing Settings() false-positive unrelated)
E2E vs emulator: drove _check_quota/_record_usage through the full lifecycle —
  under-budget allowed → over-budget refused (window=daily) → admin raises limit
  → allowed → admin disables → refused via disabled path. All correct.
```

### ⏸ PAUSE 42 — COMPLETE

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
