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

## Current Step: → STEP 44 COMPLETE (admin-created user accounts — invite by email + temp password, forced first-login password change, delete user, Firestore-record access fallback). Phases 1–3 otherwise complete (app is deployable: Cloud Run backend + Firebase Hosting frontend); STEP 43 deployment polish still open.

---

# PHASES 1–3 — COMPLETED ✓

Done and shipped. Kept as a one-line index only — do not re-implement. The code,
`CLAUDE.md` (+ nested module docs) and `DEPLOY.md` are the source of truth.

- **STEP 1–18** — monorepo scaffold, core models, Firestore service, FastAPI +
  API-key auth, WebSocket protocol, Firebase Auth, Spiżarnia REST + chat toggle,
  React/Vite SPA (login, chat, spizarnia, shopping list, calendar, NavBar).
- **STEP 21–26, 29–30** — recipe detail modal, ShoppingListAgent, 4-option
  proposal flow, Recipe Sources tab, chat UX polish, `allow_ai_generated` gate,
  proposal images.
- **STEP 31–39** — agent architecture hardening: legacy pipeline deleted,
  `reset_turn()` lifetime contract, unit-testable `resolve_recipe`, ordered
  `events: list[TurnEvent]`, hygiene cleanup, unit/integration test split
  (`integration` marker), og:image proposal cards, web-extraction URL selection.
  *(STEP 35 skipped 2026-06-07 — prompt-coercion refactor, regression risk
  outweighed benefit. STEP 40 removed — gpt-4o-mini is not deprecated.)*
- **STEP 41** — "Znajdź w Frisco" delivery-shop matching: standalone
  `packages/delivery-shops/` (`ProductMatcher` + `FriscoShop`),
  `POST /v1/grocery/{shop}/match`, `FriscoPanel.tsx`, LLM re-rank.
- **STEP 42** — user management + per-user token quotas: `TokenQuota`/`UserRecord`
  (record stored on the `users/{uid}` parent doc), pure `models/quota.py` math
  (0 = unlimited, lazy period-keyed reset in `QUOTA_TIMEZONE`), `_check_quota`
  before / `_record_usage` after each WS turn, `quota_exceeded` WS message,
  `/v1/admin/*` + `/v1/me`, `AdminPage.tsx`.
- **STEP 27–28** — packaging + deployment: `Dockerfile`, `.dockerignore`,
  `cloudbuild.yaml` (Secret Manager wiring), `scripts/seed_admin.py`,
  `DEPLOY.md`, Firebase Hosting frontend with real Firebase Auth + rewrites,
  `ALLOWED_EMAILS` access whitelist on REST + WS.

---

# PHASE 3 — REMAINING POLISH

## STEP 43 — Deployment loose ends

- [ ] `docker-compose.yml` — add a `tastyhub` service alongside the firestore
      emulator so the whole stack runs with one `docker-compose up --build`
      (today compose only starts the emulator; the container is only exercised
      via Cloud Build).
- [ ] `README.md` — add a deployment section pointing at `DEPLOY.md` (README
      currently says nothing about deploying).
- [ ] `infrastructure/scripts/setup_gcp.sh` — one-time GCP project setup
      (enable APIs, create secrets, service accounts). Currently these steps
      only exist as prose in `DEPLOY.md`.

### Verify

```bash
docker-compose up --build
curl http://localhost:8000/health
```

### ⏸ PAUSE 43

---

## STEP 44 ★ — Admin-created user accounts (invite by email + temp password)

**Goal:** An admin can create a new account straight from the admin panel by
typing an email — the backend creates the Firebase user with a generated
temporary password, shows that password **once** to the admin, and forces the
new user to set their own password on first login. Today there is no way to
onboard a user at all: accounts must be hand-created in the Firebase console
*and* their email added to the `ALLOWED_EMAILS` env var, which needs a redeploy.

### Current state (as audited 2026-07-25 — BEFORE this step; historical)

> Kept as the record of why this step existed. Everything under "Does NOT exist"
> was closed by STEP 44 itself — the code + `CLAUDE.md` are the source of truth.

Exists (at audit time):
- `app/api/admin.py` — `require_admin`-gated routes: `GET /v1/admin/users`,
  `PUT /v1/admin/users/{uid}/quota|role|disabled`, plus `GET /v1/me` (`MeView`).
  There is **no create and no delete** route.
- `models/user.py` — `UserRecord` (uid, email, role, quota, disabled) and
  `TokenQuota` (0 ⇒ unlimited). No `display_name`, no password-state field.
- `services/firestore.py` — `get_user_record` (creates a default on first sight,
  seeds admins from `ADMIN_UIDS`), `save_user_record`, `list_user_records`.
  Records live on the `users/{uid}` **parent doc** so the stream sees them.
- `middleware/auth.py` — `get_current_user` verifies the Firebase ID token, then
  gates on `email_allowed(email, settings.allowed_emails)`; `get_user_record` /
  `require_admin` build on it. `firebase_admin` app is initialised lazily via
  `_get_firebase_app()`.
- `auth_policy.py` — pure `email_allowed(email, allowed)`; empty list ⇒ open.
- `frontend/src/components/AdminPage.tsx` — the user table with inline quota
  editing + role/disable buttons. `App.tsx` resolves `/v1/me` into `isAdmin` and
  only renders the Admin tab for admins. `Login.tsx` does
  `signInWithEmailAndPassword` (with a `DEV_MODE` bypass).
- `firebase-admin>=6.5` is already a dependency — `auth.create_user()`,
  `auth.update_user()` and `auth.delete_user()` are available with no new package.

Did NOT exist (all three closed by this step):
- Any user-creation, password-set or user-delete endpoint.
- Any notion of "must change password" anywhere in the stack.
- Any Firestore-backed authorization — the whitelist was env-only.

### Design decisions (settled during planning 2026-07-25)

- **Temp password delivery: shown once in the admin panel.** The create response
  carries the generated password; the SPA displays it in a copy-able one-time
  panel and never refetches it. Rationale: no email infrastructure to configure
  or deploy, and STEP 27/28 deployment work is still in flight. A Firebase
  password-reset email is the natural follow-up, deferred below.
- **Password generation is server-side and pure.** `models/password.py` exposes
  `generate_temp_password()` using `secrets.choice` over an unambiguous alphabet
  (no `0/O/1/l/I`), 12 chars, guaranteed ≥1 digit — keeps it unit-testable with a
  seeded/enumerated check and out of the API module.
- **Forced change is a flag on `UserRecord`, not on Firebase.** New field
  `must_change_password: bool = False`. Firebase has no such concept, so the
  server owns it: set `True` at creation, cleared when the user completes the
  change. Rationale: it rides along on the record already fetched by every
  request, so enforcement costs no extra read.
- **Enforcement is server-side, not just UI.** A new dependency
  `require_password_set` (wrapping `get_user_record`) returns **423 Locked** when
  `must_change_password` is set. Applied to the chat/product routes and the WS
  handshake — a hidden UI screen is not access control.
- **Password change goes through the backend, not the Firebase client SDK.**
  `POST /v1/me/password` takes the new password, calls
  `firebase_admin.auth.update_user(uid, password=…)` in `asyncio.to_thread()`
  (blocking SDK — Architecture Rule 4), then clears the flag. Rationale: the
  client SDK's `updatePassword` cannot clear a server-side flag atomically, and
  we'd have to trust the client to report success.
- **Existence of a `UserRecord` grants access.** `get_current_user` still checks
  `email_allowed`, but on a `False` result falls through to a Firestore lookup:
  an existing, non-disabled record for that uid authorizes the caller. Rationale:
  admin-created users must work without a redeploy. `auth_policy.email_allowed`
  stays pure and untouched; the Firestore fallback lives in the middleware.
  **Note the ordering trap:** `get_current_user` currently raises before any
  Firestore access, and it has no `Request` — it must take `request: Request` to
  reach `app.state.firestore`.
- **Create form sets role, quota and display name** (user's choice). Display name
  is stored on the Firebase account *and* mirrored onto `UserRecord.display_name`
  so the admin table can render it without an extra Firebase read.
- **Creation is idempotent-ish, not silently overwriting.** If the email already
  has a Firebase account, return **409 Conflict** with a clear Polish message
  rather than resetting a live user's password.
- **Delete is in scope.** Without it, a mistyped email is unfixable from the
  panel. `DELETE /v1/admin/users/{uid}` removes the Firebase user and the
  Firestore record; refuses (409) when the target is the calling admin.

### Tasks

- [x] **Core models** — `models/password.py`: `generate_temp_password(length=12)`
      (pure, `secrets`-backed, unambiguous alphabet) and
      `validate_password(pw) -> str | None` returning a Polish error or `None`
      (min 8 chars). `models/user.py`: add `display_name: str | None = None` and
      `must_change_password: bool = False` to `UserRecord` (both defaulted, so
      existing Firestore docs deserialize unchanged).
- [x] **Firestore service** — `services/firestore.py`: `delete_user_record(uid)`
      (deletes `users/{uid}`); extend `get_user_record` to accept/persist
      `display_name`. Doc path is unchanged: `users/{uid}` parent doc.
- [x] **Agent/tool** — **untouched.** This is CRUD; no LLM call, no new agent, no
      per-turn token cost.
- [x] **Protocol** — `protocols/ws_messages.py` **untouched**. No new push to the
      browser; the only WS change is the handshake rejecting a locked account
      (reuse the existing `error` message + close).
- [x] **REST API** — `app/api/admin.py`:
      - `POST /v1/admin/users` (`require_admin`) — body
        `{email, display_name?, role, daily_limit, monthly_limit}`; calls
        `auth.create_user(email=…, password=<generated>, display_name=…)` via
        `asyncio.to_thread`, writes the `UserRecord` with
        `must_change_password=True`, returns `CreatedUserView {record,
        temp_password}`. 409 on `EmailAlreadyExistsError`, 422 on invalid email.
      - `DELETE /v1/admin/users/{uid}` (`require_admin`) — `auth.delete_user` +
        `delete_user_record`; 409 if `uid == caller.uid`.
      - `POST /v1/me/password` (`get_user_record`, **not** `require_password_set`
        — this is the one route a locked user may call) — body `{new_password}`;
        validates, `auth.update_user(uid, password=…)`, clears
        `must_change_password`, returns the updated `UserRecord`.
      - `MeView`: add `must_change_password` and `display_name`.
- [x] **Auth middleware** — `middleware/auth.py`: `get_current_user` takes
      `request: Request` and, when `email_allowed` is `False`, falls back to an
      existing non-disabled `UserRecord`; add `require_password_set` (423 when
      the flag is set) and apply it to the product routes (`sessions`,
      `spizarnia`, `search_prefs`, `shopping_list`, `grocery`) and the WS
      handshake in `api/websocket.py`.
- [x] **Env / config** — **no new env vars.** `ADMIN_UIDS`,
      `DEFAULT_DAILY_TOKEN_LIMIT`, `DEFAULT_MONTHLY_TOKEN_LIMIT` and
      `ALLOWED_EMAILS` all keep their meaning; document in root `CLAUDE.md` that
      `ALLOWED_EMAILS` is now a *bootstrap* whitelist and an existing
      `UserRecord` also grants access.
- [x] **Frontend** — `AdminPage.tsx`: "Dodaj użytkownika" form (email, display
      name, role select, two quota inputs) + a one-time result panel showing the
      temp password with a copy button and an explicit "nie zobaczysz go
      ponownie" warning; a delete button per row with a confirm. New
      `ChangePassword.tsx` screen rendered by `App.tsx` **instead of** the main
      shell while `me.must_change_password` is true. `types.ts`: extend
      `UserRecord`/`MeView`, add `CreatedUserView`. Polish copy for the new
      screen goes in `models/ui_strings.py` and flows through `/v1/ui`.
- [x] **Tests:**
  - Core unit `packages/cookbot-core/tests/test_password.py` — generated
    passwords hit the length/charset/digit contract over many draws and are not
    repeated; `validate_password` accepts/rejects the boundary cases.
  - Core unit — `UserRecord` round-trips with the new fields defaulted from a
    legacy dict (no `display_name` / `must_change_password` keys).
  - Client unit `clients/tastyhub/tests/test_admin_create_user.py` — with
    `firebase_admin.auth` patched and the Firestore service an `AsyncMock`:
    create returns a temp password + a record with the flag set; duplicate email
    ⇒ 409; non-admin ⇒ 403; self-delete ⇒ 409.
  - Client unit `test_password_change.py` — `POST /v1/me/password` clears the
    flag and calls `update_user`; a too-short password ⇒ 422; a locked user gets
    423 from a product route but **not** from `/v1/me/password`.
  - Client unit `test_auth_firestore_fallback.py` — an email off
    `ALLOWED_EMAILS` with an existing record is allowed; with a `disabled`
    record is 403; with no record is 403.
  - Integration — **none needed**; everything here is mockable.

### Implementation notes (deviations from the plan, decided during the build)

- **`require_password_set` was NOT applied to `shopping_list` / `grocery`.** Both
  routes carry no user identity at all (they are stateless computations over an
  ingredient list in the body, reachable with only the widget's API key), so a
  `get_user_record` dependency would have broken the anonymous path without
  protecting any user data. `sessions` and the WS handshake resolve identity
  themselves, so they call a new record-only helper `record_is_locked()` instead
  of the dependency. The effective gate is unchanged: a locked account cannot
  create a session or open a socket, and `spizarnia` / `search_prefs` return 423.
- **New `FirestoreService.find_user_record(uid)`** — a read-only lookup added
  because `get_user_record` *creates* a default record on first sight, which in
  an authorization path would have turned any uid with a valid token into an
  authorized one (silently voiding `ALLOWED_EMAILS`).
- **`pydantic.EmailStr` not used** — `email-validator` is not installed and the
  plan called for no new dependencies. Email shape is checked with a minimal
  guard plus Firebase's own `create_user` validation (`ValueError` ⇒ 422).
- **`ruff format .` was not applied repo-wide.** It reformats 31 pre-existing
  files (it collapses the codebase's aligned trailing-comment style), which would
  have buried this feature's diff. `ruff check` passes on both packages.

### Deferred within this feature

- **Emailing the temp password / Firebase password-reset link.** Chosen against
  above; revisit once an email sender is configured. When it lands it is additive:
  a second create-mode that skips returning the password.
- **Password strength rules beyond a length minimum** (complexity classes,
  breach-list check) — Firebase already rejects <6 chars server-side.
- **Bulk / CSV invite** — one user at a time is enough at this scale.
- **Audit log of admin actions** (who created/deleted whom, when) — worth doing
  before this is multi-admin in production.
- **Temp-password expiry.** The flag never times out; a created-but-never-used
  account stays pending indefinitely. Admin can delete it.
- **Cascade delete of user subcollections.** `DELETE /v1/admin/users/{uid}`
  removes the Firebase user and the `users/{uid}` parent doc, but Firestore does
  **not** cascade — `users/{uid}/spizarnia/items`, `users/{uid}/prefs/search` and
  the usage counters survive as orphans. Harmless today (nothing reads them
  without a `UserRecord`, and Firebase never reuses a uid) but it is silent data
  retention after an account is deleted. Fix = delete the known subcollection
  paths in `delete_user_record`, or a sweep job. See the docstring on
  `FirestoreService.delete_user_record`.

### Verify

```bash
cd packages/cookbot-core && uv run pytest -m "not integration" -q
cd clients/tastyhub     && uv run pytest -q
uv run ruff check . --fix && uv run ruff format . && uv run pyright
cd frontend && npx tsc --noEmit && npm test
```

### ⏸ PAUSE 44

---

# PHASE 4 — DEFERRED

Do not implement until Phase 3 is live in production.

- `○` GCS blob cache for the Frisco feed/index (~50 MB re-downloaded + rebuilt
  on every Cloud Run cold start today — deferred out of STEP 41)
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
