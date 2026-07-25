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

## Current Step: → STEP 47 ✔ (fast path: zero-LLM DuckDuckGo search for plain recipe requests — measured 11.73s → 3.50s, 2629 tokens → 0, 6 cards). STEP 46 ✔; STEP 44 COMPLETE (admin-created user accounts). Phases 1–3 otherwise complete (app is deployable: Cloud Run backend + Firebase Hosting frontend); STEP 43 deployment polish still open.

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

## STEP 45 ★ — Multi-recipe pages: ask whether to split

**Goal:** When a fetched page contains more than one recipe, stop silently
merging them. Ask the user whether to keep them as one recipe or split them into
separate ones — unless the extra recipe is a small *component* (a sauce, a
dressing, a marinade), which should stay folded into the main recipe without
asking.

### Why (found live, 2026-07-25)

`chilitonka.com/.../prawdopodobnie-najlepsze-curry-...-chlebkiem-naan/` hosts
**two independent recipes** under one URL: the curry ("Składniki dla 4 osób")
and naan bread ("Składniki na 8 porcji"). Extraction is faithful per
`agents/CLAUDE.md` Rule 5, so it correctly captures everything — and returns a
single `Recipe` with **21 ingredients and 17 steps** whose `servings=4`.

Two things then go wrong downstream:
- **The shopping list is wrong.** A 4-person curry request also buys 8 portions
  of naan (500 g flour, 110 g butter, yeast) with no indication why.
- **`servings` is ambiguous.** One integer cannot describe "curry for 4 + bread
  for 8", so scaling to a different serving count silently rescales both by the
  curry's ratio.

This is *not* an extraction bug — do not "fix" it by teaching the extractor to
drop the second recipe. The page really does contain both; the product question
is what the user wants, which only the user can answer.

### Design decisions to settle before coding

- **Component vs. standalone.** A second ingredient block is a *component* (fold
  in silently) when it has no serving count of its own, or its serving count
  matches the main recipe, or its heading names a part of the dish
  (sos/dressing/marynata/polewa/krem/farsz). It is *standalone* (ask) when it has
  its own distinct serving count — as naan does with "na 8 porcji" — and reads as
  a dish that could be cooked alone. Prefer this heuristic over an
  ingredient-count threshold: "8 porcji" is the signal that actually
  distinguished the two blocks here.
- **Where the split decision lives.** Extraction must stay verbatim, so the
  extractor's job is only to *report* the blocks it saw; the ChatAgent decides
  whether to ask. Likely shape: the fetch agent gains an optional
  `components: list[RecipeComponent]` (name + servings + ingredients + steps) on
  its output, and a ChatAgent tool asks the user when >1 standalone block came
  back. Confirm this against `agents/CLAUDE.md` Rule 1 (new capability = a
  ChatAgent tool) before implementing.
- **How the question reaches the user.** There is no generic "ask the user a
  question" event today — the ChatAgent asks in prose and reads the next turn's
  reply. Decide whether that is enough (cheapest, matches guided onboarding) or
  whether this needs a typed choice event + a frontend affordance. Note the
  reply must survive a reconnect: whatever holds the pending question must be in
  the `ChatState` snapshot (Architecture Rule 3), not a module global.
- **What "split" produces.** Two `FinalRecipeEvent`s / two calendar entries, or
  one primary recipe plus a linked side? This decides whether `add_to_calendar`
  needs to handle a set. Both keep the same `source_url` (Rule 5).

### Acceptance criteria

- [ ] A page with one recipe behaves exactly as today — **no question asked**,
      no extra LLM call on the common path.
- [ ] A page whose second block is a sauce/dressing (no own serving count) folds
      into the main recipe silently, as today.
- [ ] The chilitonka curry+naan page asks the user which they want.
- [ ] Choosing "split" yields a curry recipe with `servings=4` whose ingredients
      contain no flour/yeast, and a separate naan recipe with `servings=8`.
- [ ] Choosing "keep together" reproduces today's merged behaviour.
- [ ] The shopping list for a 4-person curry contains no naan ingredients once
      split.
- [ ] `source_url` is preserved on every recipe produced (Rule 5).
- [ ] Unit tests with `TestModel` for the component-vs-standalone heuristic
      (pure function — test it directly, no LLM).
- [ ] Live e2e extending
      `tests/integration/test_url_servings_calendar_live.py`, which already pins
      this page and currently asserts the merged 21-ingredient behaviour —
      **update those assertions in the same commit.**

### Verify

```bash
cd packages/cookbot-core && uv run pytest -m "not integration" -q
cd packages/cookbot-core && uv run pytest -m integration tests/integration/test_url_servings_calendar_live.py -q
cd clients/tastyhub     && uv run pytest -q
uv run ruff check . --fix && uv run pyright
```

---

## STEP 46 ✔ — Fix `test_direct_recipe_request_skips_onboarding`

**Goal:** Make the failing live e2e test pass, or correct the assertion if the
test is wrong about what the product should do.

> **DONE 2026-07-25.** The assertion was right and the test was pointing at a
> real bug. `propose_recipes` *received* `dish_type`/`servings` as arguments,
> used them for the search, and **discarded them** — it never wrote to
> `deps.onboarding`, which is the only place `resolve_recipe` /
> `get_recipe_from_url` read `servings` from when scaling. So a direct "dla N
> osób" request scaled to the `or 2` default: right by luck for "dla 2 osób",
> silently wrong for every other number (verified live — "dla 6 osób" recorded
> `None` before, `6` after).
>
> Fixed on both fronts the step called for:
> - **Deterministic (the real fix):** `propose_recipes` now writes its arguments
>   back to `deps.onboarding`, filling blanks only — never overwriting an answer
>   guided onboarding already collected (unit-tested both ways).
> - **Prompt:** §0b said to skip the onboarding *questions*, which gpt-4o-mini
>   read as "skip the tool". It now says to skip the questions but still call
>   `update_onboarding` with what the message already gave.
>
> Verified: target test green on 3 consecutive runs; all 3 chat e2e tests pass
> (incl. `test_full_onboarding_to_web_recipe`, the guided-path regression);
> 194 core + 91 client unit tests; extraction + STEP 45 e2e unaffected.

### Current state (verified 2026-07-25)

`tests/integration/test_chat_e2e_live.py::test_direct_recipe_request_skips_onboarding`
fails on `assert deps.onboarding.dish_type` — the field is `None` after the turn.
**Pre-existing, not caused by the STEP 45 work**: confirmed by stashing all local
changes and re-running against a clean tree, where it fails identically.

What actually happens on "Przepis na halloumi dla 2 osób":
- The agent **does** skip onboarding and go straight to proposals — 4 real
  `web_search` proposals come back, which is the behaviour the test is named for.
- But it reaches them **without calling `update_onboarding`**, so
  `deps.onboarding` stays entirely empty (`dish_type`, `servings` both `None`).

So the routing works and the *state recording* does not.

### The real question to settle first

Is the empty `onboarding` a bug, or is the assertion too strict?

It matters beyond this test: `deps.onboarding.servings` is what
`get_recipe_from_url` and `resolve_recipe` scale to. If a direct request never
records `servings=2`, a later "dodaj do kalendarza" for that dish scales to the
`or 2` default by luck rather than by the user's stated "dla 2 osób" — ask for 6
and the recipe silently stays at 2. Check that path before weakening the test.

Likely fix: make the system prompt require `update_onboarding` alongside
`propose_recipes` on a direct request (§0b currently tells the model to skip the
*questions*, which gpt-4o-mini reads as "skip the tool"). Prefer a prompt fix to
a code workaround, but if the model stays unreliable, extracting dish/servings
from the request into `deps` in the `propose_recipes` tool is a legitimate
deterministic fallback.

### Acceptance criteria

- [ ] Root cause stated in one line in the commit message: prompt, tool wiring,
      or over-strict assertion.
- [ ] A direct "dla N osób" request results in `deps.onboarding.servings == N`,
      **or** the test is changed with a comment explaining why that is not
      required and the scaling path is shown to be unaffected.
- [ ] `test_direct_recipe_request_skips_onboarding` passes on 3 consecutive runs
      (live tests are flaky by nature — one green run proves nothing).
- [ ] No regression in `test_full_onboarding_to_web_recipe`, which depends on the
      guided path still filling the same fields turn by turn.

### Verify

```bash
cd packages/cookbot-core && uv run pytest -m integration tests/integration/test_chat_e2e_live.py -q
cd packages/cookbot-core && uv run pytest -m "not integration" -q
```

---

## STEP 47 ✔ — Fast path: zero-LLM web search for plain recipe requests

> **DONE 2026-07-25.** Measured A/B on "jagodzianki" (live, same machine):
> **11.73s → 3.50s (3.35x), ~2629 tokens → 0, 4 cards → 6.** Per-dish live runs:
> jagodzianki 3.44s, pierogi ruskie 4.85s, żurek 5.50s; image coverage a
> consistent 5/6 (the miss is przepisy.pl, which hard-blocks bots with a 403).
>
> Four deviations from the plan, all driven by what the live runs showed:
> 1. **`og:description` + JSON-LD enrichment was added** (the plan deferred all
>    metadata). Both come from bytes already fetched for `og:image`, so they are
>    free — where a page ships schema.org/Recipe the chips fill with REAL page
>    data instead of staying empty.
> 2. **`enrich_from_page_head` streams and aborts** at `_HEAD_MAX_BYTES` instead
>    of awaiting `resp.text`. Recipe pages are 125-158 kB while the metadata sits
>    at offsets 1.6k-17.5k; the full download was costing image coverage (3/6 →
>    5/6 after the fix) and most of the enrichment budget.
> 3. **The enrichment stage is capped as a whole** (`_ENRICH_TOTAL_BUDGET_SECONDS`),
>    not just per-request — six concurrent fetches are bounded by the slowest
>    host, which pushed "żurek" from 2.9s to 6.2s once.
> 4. **HTML entities are decoded** (`html.unescape`) in card text and image URLs.
>    Observed live: "Ponad 10 najlepszych przepis&#243;w na żurek", and `&amp;`
>    in an og:image query string breaking the `<img src>`.
>
> Also added `tests/test_agents/conftest.py`: an autouse fixture that neuters the
> fast path in the unit tier. Without it a test that stubs only the LLM path makes
> REAL DuckDuckGo calls — `test_propose_recipes_failure_returns_structured_error`
> was fetching 20 live results before the guard existed.
>
> The live latency test's budget is deliberately loose (8s): DDG itself is a
> ~1.9-2.8s floor outside our control, so a tight bound would be flaky. It guards
> against the fast path silently not engaging (~12s), not against search jitter.

**Goal:** When the user just names a dish with no extra requirements ("znajdź
przepis na jagodzianki"), return **6 recipe cards straight from DuckDuckGo with
no LLM call inside the tool** — target ~2s instead of today's ~8–15s. The card
layout stays exactly as it is (rectangle + og:image photo); only the metadata
chips become optional, because a zero-LLM card cannot invent difficulty or
cooking time. Vague or constrained requests keep today's RecipeOptionsAgent path
unchanged.

### Current state (audited 2026-07-25)

**The latency chain for "znajdź przepis na jagodzianki" today** — four sequential
stages, three of which are LLM round-trips:

1. **ChatAgent turn** (`chat.py:836`) — the model reads the DIRECT RECIPE REQUEST
   prompt (`onboarding_status_prompt`, `chat.py:596-621`) which instructs it to
   call `update_onboarding` *then* `propose_recipes`. That is two tool
   round-trips before the search even starts.
2. **`propose_recipes` → RecipeOptionsAgent** (`recipe_options.py:71-125`) — the
   dominant cost. It is a full agentic loop: LLM call → `duckduckgo_search` tool
   → second LLM call that *writes* 4 proposals, each with a name, a 1–2 sentence
   Polish description, difficulty, `total_time_minutes`, and 3–5
   `key_ingredients`. That is ~4×80 tokens of generated prose on `gpt-4o-mini`.
3. **`populate_proposal_images`** (`recipe_options.py:44-64`) — concurrent
   best-effort og:image scrape, `_OG_FETCH_TIMEOUT = 6.0`. Already fast and
   already deterministic; **this is the piece to reuse verbatim.**
4. **ChatAgent streams a closing sentence** — one more LLM round-trip.

**What already exists and can be reused:**
- `populate_proposal_images()` — concurrent og:image fetch, in-place,
  best-effort. Works on any `list[RecipeSummary]`; needs no change.
- `RecipeSummary` (`models/recipe.py:37-45`) — already has every field the fast
  path needs, including `source_url` and `image_url`.
- `_select_proposal` (`chat.py:376-396`) — maps "2" or a name onto a proposal and
  is already length-agnostic (`0 <= idx < len(proposals)`), so **6 cards need no
  change here.**
- `WsMessageType.RECIPE_OPTIONS` + `ws_send_recipe_options`
  (`protocols/ws_messages.py:23,186`) — already carries `list[RecipeSummary]`,
  any length. **No protocol change needed.**
- `RecipeOptionsEvent` + the `_emit_event` arm — unchanged.
- `resolve_recipe` (`chat.py:416`) — the pick path (`get_recipe_details`) is
  untouched; a fast-path card carries a real `source_url`, so picking one goes
  straight to `build_web_fetch_agent(pinned_url=...)` exactly as today.
- `ddgs.DDGS().text()` — reachable directly from Python. PydanticAI's
  `duckduckgo_search_tool` is only a thin `anyio.to_thread` wrapper around it
  (verified in `pydantic_ai/common_tools/duckduckgo.py`), so calling `DDGS`
  ourselves needs no new dependency.

**What does NOT exist:**
- Any code path that produces proposals without an LLM call.
- Any deterministic recipe-URL filter — the "prefer `/przepis/`, avoid forums and
  listicles" logic currently lives **only as prose in the RecipeOptionsAgent
  prompt** (`recipe_options.py:88-99`) and must be ported to Python.
- Conditional rendering of the metadata chips. `ChatPanel.tsx:455-456` renders
  `⏱ {p.total_time_minutes} min · {p.difficulty}` and the `key_ingredients` line
  unconditionally, so empty values would show as "⏱ 0 min · ".

### Design decisions (settled during planning 2026-07-25)

- **Zero-LLM cards:** `name` and `description` come verbatim from the DDG result
  `title` / `body`; `difficulty=""`, `total_time_minutes=0`, `key_ingredients=[]`
  — and the frontend hides those chips when empty. Chosen over a cheap enrichment
  pass because the enrichment call is exactly the round-trip this STEP exists to
  remove, and a title+photo card is honest: everything on it came from a real
  page. **Never let a model fill these in later** — that reintroduces the
  fabrication risk the verbatim-extraction rule exists to prevent.
- **Trigger = concrete dish AND no constraints.** The fast path runs only when
  `dish_type` is concrete (reuse `OnboardingState.has_concrete_dish()`) **and**
  `ingredients`, `dietary_hints`, `max_time_minutes`, and `free_notes` are all
  empty. Anything else — including "jagodzianki bez cukru" — falls through to the
  RecipeOptionsAgent, which can actually reason about the constraint. A search
  keyword is not the same as an honoured requirement.
- **6 on the fast path, 4 on the LLM path.** Search results are free, so 6 costs
  nothing extra; each LLM-written proposal costs tokens, so the slow path stays
  at 4. Both counts become `TenantConfig` fields rather than literals, so they
  are tunable per tenant without a code change.
- **Fallback is silent and automatic.** If the deterministic filter yields fewer
  than `proposal_min_fast` (3) usable URLs, fall through to the RecipeOptionsAgent
  in the same tool call. The user sees a slower-but-normal result, never an
  error. This also covers a DDG outage or rate-limit.
- **Trim the ChatAgent prompt, keep the agent orchestrating.** Hard Rule 1 stands:
  no bypassing the ChatAgent. But §0b of the instructions
  (`chat.py:706-712`) and the DIRECT RECIPE REQUEST branch currently mandate a
  *separate* `update_onboarding` call before `propose_recipes`. Since STEP 46,
  `propose_recipes` already records dish/servings into `deps.onboarding` itself
  (`chat.py:869-878`), so that first round-trip is now redundant — fold it into a
  single `propose_recipes` call. **STEP 46's guarantee must survive**: a direct
  "dla N osób" request must still end with `deps.onboarding.servings == N`.
- **`source="web_search"` on fast-path cards.** They are real web pages, so the
  existing "web" badge and `Źródło ↗` link are correct as-is.
- **Model choice is out of scope, but recorded:** the user raised trying a newer
  OpenAI model than `gpt-4o-mini`. The fast path removes the LLM from the search
  entirely, so the model no longer affects *this* latency — it still affects the
  ChatAgent turn. Benchmark `model_chat` separately; do not bundle a model
  migration into this STEP or the latency measurement becomes unattributable.

### Tasks

- [ ] **Core config** — `models/tenant.py`: `proposal_count: int = 4`,
      `proposal_count_fast: int = 6`, `proposal_min_fast: int = 3`. Mirror in
      `clients/tastyhub/app/config/settings.py` + `tenant.py`, `.env.example`,
      and the root CLAUDE.md env table.
- [ ] **Deterministic URL filter** — new `agents/recipe_search_fast.py`, pure and
      I/O-free so it unit-tests without network (the `models/quota.py` pattern):
  - `_RECIPE_URL_HINTS` (`/przepis/`, `/przepisy/<slug>`, `/recipe/`) → rank first.
  - `_BLOCKED_PATTERNS` — forums, `/tag/`, `/kategoria/`, `/search`, bare
      homepages, and the listicle/lifestyle domains named in the current prompt
      (`ofeminin.pl` etc.). Port the prose rules from `recipe_options.py:88-99`.
  - Dedupe by domain so 6 cards are 6 different sites where possible.
  - `score_and_rank(results, limit) -> list[DuckDuckGoResult]`.
- [ ] **Fast search function** — same module:
      `async def fast_recipe_proposals(query, *, limit, site_filter) -> list[RecipeSummary]`.
      Calls `DDGS().text()` via `asyncio.to_thread` (Architecture Rule 4 —
      `ddgs` is a blocking client), ranks, maps title/body → `name`/`description`,
      sets `source="web_search"`, `source_url=href`, leaves the metadata fields
      empty. **No `Agent`, no model call anywhere in this module.**
- [ ] **Wire into `propose_recipes`** — `agents/chat.py`: before building the
      RecipeOptionsAgent, evaluate the trigger predicate; on a hit call
      `fast_recipe_proposals`, then reuse `populate_proposal_images` unchanged.
      Fall through to the existing path when the trigger misses **or** fewer than
      `proposal_min_fast` results survive. Log `propose_recipes_fast_path` with
      `hit`/`count`/`elapsed_ms` so the speedup is measurable in Cloud Logging.
      Keep the whole thing inside the existing `try/except` — Hard Rule 7.
- [ ] **Trim the ChatAgent prompt** — `agents/chat.py`: in the DIRECT RECIPE
      REQUEST branch of `onboarding_status_prompt` and §0b of the agent
      instructions, drop the mandatory separate `update_onboarding` step and tell
      the model to call `propose_recipes` directly with every detail the message
      gave (`dish_type`, `servings`, …).
- [ ] **Protocol** — **untouched.** `WsMessageType.RECIPE_OPTIONS` already carries
      a variable-length `list[RecipeSummary]`.
- [ ] **REST API** — **untouched.** This is a chat-turn feature only.
- [ ] **Firestore** — **untouched.** Proposals live in `deps.last_proposals` and
      the existing `ChatState` snapshot, which is already a list.
- [ ] **Frontend** — `frontend/src/components/ChatPanel.tsx`: render the
      `⏱ … · difficulty` line only when `total_time_minutes > 0 || difficulty`,
      and the `key_ingredients` line only when non-empty. Confirm
      `styles.optionsGrid` reflows to 6 cards without overflow.
- [ ] **Tests:**
  - Core unit `tests/test_agents/test_recipe_search_fast.py` — ranking puts
      `/przepis/` URLs first; forum/tag/homepage/listicle URLs are dropped;
      domain dedupe; fewer-than-min returns a short list; title/body map onto
      `name`/`description`; metadata fields stay empty. All pure, no network.
  - Core unit — `propose_recipes` trigger matrix with a stubbed
      `fast_recipe_proposals`: concrete dish + no constraints → fast path taken
      and RecipeOptionsAgent **never built**; each constraint present → slow path;
      `dish_type="any"` → slow path; fast path returning 2 → falls back to slow.
      Use `TestModel` for the ChatAgent, per the house rule.
  - Core unit — 6 proposals round-trip through `_select_proposal` ("6" and a name
      both resolve) and `dump_chat_state`/`restore_chat_state`.
  - Integration (live, `-m integration`) — extend
      `tests/integration/test_recipe_options_live.py`: "znajdź przepis na
      jagodzianki" yields ≥3 proposals, every one with a real `source_url`, and
      **asserts wall-clock elapsed < 5s** — the acceptance criterion of this STEP
      is latency, so it needs a real assertion, not a vibe.
  - Integration (live) — the STEP 46 guarantee still holds: a direct "dla 4 osób"
      request ends with `deps.onboarding.servings == 4` after the prompt trim.

### Deferred within this feature

- **Streaming enrichment** (send bare cards, then patch in metadata via a second
  WS message) — rejected for now: needs a new `WsMessageType` plus merge-by-index
  logic in `ChatPanel`, and reintroduces the LLM call this STEP removes.
- **Applying the fast path to constrained requests** ("jagodzianki bez cukru") —
  a constraint appended to a DDG query is a keyword, not an honoured requirement.
  Revisit only with evidence that DDG respects it.
- **Bumping the LLM path to 6 proposals** — costs tokens against every user's
  quota (STEP 42) for no latency win.
- **Newer OpenAI model for `model_chat`** — benchmark separately, so this STEP's
  latency improvement stays attributable.
- **Caching DDG results per dish** — worth doing if repeat queries turn out to be
  common, but needs Firestore/GCS and belongs with the Phase 4 blob-cache work.

### Verify

```bash
cd packages/cookbot-core && uv run pytest -m "not integration" -q
cd packages/cookbot-core && uv run pytest -m integration tests/integration/test_recipe_options_live.py -q
cd packages/cookbot-core && uv run pytest -m integration tests/integration/test_chat_e2e_live.py -q
cd clients/tastyhub     && uv run pytest -q
cd frontend             && npx tsc --noEmit
uv run ruff check . --fix && uv run pyright
```

### ⏸ PAUSE 47

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
