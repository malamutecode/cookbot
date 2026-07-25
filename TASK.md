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

## Current Step: → STEP 45

**STEP 45** (multi-recipe pages: ask whether to split) is the only unstarted
feature step. **STEP 49 ✔** (portion counts visible + trustworthy) ·
**STEP 48 ✔** (meal slots + drag-and-drop, `9fdb8ba`) ·
**STEP 47 ✔** (zero-LLM fast path: 11.73s → 3.50s, 2629 tokens → 0, 6 cards,
`eaa984f`) · **STEP 46 ✔** · **STEP 44 ✔**. Phases 1–3 are otherwise complete —
the app is deployable (Cloud Run backend + Firebase Hosting frontend, scripted in
`infra/`). **STEP 43** has two small loose ends left.

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
- [ ] `README.md` — add a deployment section pointing at `DEPLOY.md` +
      `infra/README.md` (README currently says nothing about deploying).
- [ ] `pyright` as a dev dependency — it is in neither `pyproject.toml`, so the
      `uv run pyright` that CLAUDE.md and several steps call for fails with
      "program not found" and everyone falls back to `npx -y pyright@latest`.
      Baseline when last measured (2026-07-25): **10 pre-existing errors**, in
      `test_recipe_search_fast.py`, `test_url_servings_calendar_live.py` and
      `test_chat.py` — pin the config so that baseline is enforced, not just known.
- [x] ~~`infrastructure/scripts/setup_gcp.sh` — one-time GCP project setup~~ —
      **done differently** (`6fc298f`): `infra/bootstrap.sh` enables APIs and
      creates Firestore, Artifact Registry, secrets and IAM, idempotently, with
      `infra/deploy-backend.sh` + `deploy-frontend.sh` for releases. There is no
      `infrastructure/` directory; `infra/README.md` documents all three.

### Verify

```bash
docker-compose up --build
curl http://localhost:8000/health
```

### ⏸ PAUSE 43

---

## STEP 44 ✔ — Admin-created user accounts (invite by email + temp password)

> **DONE 2026-07-25** (`95895fc`). An admin creates an account from the admin
> panel by typing an email; the backend creates the Firebase user with a
> generated temp password, shows it **once**, and forces a password change on
> first login. Before this, onboarding a user meant hand-creating them in the
> Firebase console *and* adding their email to `ALLOWED_EMAILS` — a redeploy.
>
> Shipped: `models/password.py`, `UserRecord.display_name` +
> `must_change_password`, `POST /v1/admin/users`, `DELETE /v1/admin/users/{uid}`,
> `POST /v1/me/password`, `require_password_set` (423), a Firestore-record
> fallback in `get_current_user`, the `AdminPage.tsx` create form and
> `ChangePassword.tsx`. Protocol and agents untouched.

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
uv run ruff check . --fix && npx -y pyright@latest
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
uv run ruff check . --fix && npx -y pyright@latest
```

---

## STEP 48 ✔ — Meal slots in the calendar + shopping-list button row

**Goal:** Split every calendar day into `Notatki` + 4 meal sections (Śniadanie,
Lunch, Obiad, Kolacja) so a week reads as a real meal plan, and let the user drag
dishes between slots and days. Add per-dish selection so a shopping list can be
built from chosen meals rather than whole days, and add a "clear the whole list"
button to the shopping-list panel with the button row rearranged.

> **DONE 2026-07-25** (`9fdb8ba`). Shipped: `MealSlot` StrEnum +
> `CalendarEntry.meal_slot` (defaulting to `obiad`, so slot-less `localStorage`
> entries keep working), a `meal_slot` arg on `add_to_calendar`, the new
> `ui_strings` copy, `frontend/src/lib/calendar.ts` (move/selection reducers,
> unit-tested under `node:test`), slot sections + move-on-drop + per-dish
> checkboxes in `CalendarPage.tsx`, and `clearAll()` in `ShoppingList.tsx`.
>
> The audit shrank the feature twice: the calendar is `localStorage`-only, so no
> persistence work was needed; and `WsOutCalendarUpdate` already nests the whole
> `CalendarEntry`, so protocol and REST were untouched.

### Design decisions (settled during planning 2026-07-25)

- **Slot IDs are stable English keys** (`sniadanie`, `lunch`, `obiad`, `kolacja`);
  Polish labels come from `ui_strings.py` — persisted `localStorage` entries must
  not break when copy changes.
- **`Notatki` is not a slot.** It stays `day.freeText`, rendered as the first
  section. It holds no recipes, so no checkbox and it is never a drop target.
- **Legacy entries default to `obiad`** on read (`entry.mealSlot ?? 'obiad'`), not
  by rewriting `localStorage` — slot-less data keeps working untouched.
- **The agent picks the slot** — `add_to_calendar` gains an optional `meal_slot`
  enum arg defaulting to `obiad`, plus one prompt rule, so "śniadanie w środę"
  lands correctly. Cost is one enum in an existing tool schema: **no new LLM call
  and no measurable per-turn token increase** (STEP 42 quotas unaffected).
- **Drag is move, not copy** — payload `{entryId, fromDate, fromSlot}` with
  `effectAllowed = 'move'`; the drop removes from source before inserting. Copy
  semantics would duplicate a dish across slots.
- **Two independent selection models, two buttons** (user's explicit choice):
  the existing day checkboxes drive `Utwórz listę zakupów (N dni)`; new per-chip
  checkboxes drive `Utwórz listę zakupów (wybrane dania)`. Neither clears the other.
- **`Wyczyść wszystko` confirms** via `window.confirm` — unlike clear-checked it is
  unrecoverable. `Wyczyść zaznaczone` keeps its current no-confirm behaviour.
- **No `TenantConfig` field** — the 4 slots are a fixed product concept, not a
  per-tenant tunable. Revisit only if a client needs different meal structure.

### Deferred within this feature

- **Reordering chips within a slot** — drag targets the slot; order stays
  append-only. Full sortable lists need a drag library and are not what was asked.
- **Firestore persistence for the calendar** — it is `localStorage` today, so a
  meal plan does not follow the user across devices. Real gap, but a separate
  STEP: it needs a document shape, a sync/conflict story, and a migration.
- **Per-tenant slot configuration** (e.g. a client wanting 3 or 6 meals) — no
  demand yet; would become a `TenantConfig` field.
- **Collapsing to a single selection model** — two selection systems on one screen
  may read as cluttered once built. Revisit after seeing it in use.

### Verify

```bash
cd packages/cookbot-core && uv run pytest -m "not integration" -q
cd clients/tastyhub     && uv run pytest -q
cd frontend             && npm test && npx tsc --noEmit
cd packages/cookbot-core && uv run ruff check . --fix && npx -y pyright@latest
```

> Note the `npx` invocation — see the pyright item under STEP 43.

---

## STEP 49 ✔ — Make portion counts visible and trustworthy everywhere

**Goal:** The app already rescales a recipe's ingredients to the number of people
the user asked for — but it never *says* so, and a calendar entry doesn't record
the number at all. So a user reading "Porcje: 8" cannot tell whether the amounts
below it were actually adjusted, and a dish sitting on the calendar carries no
portion count whatsoever. This STEP makes the portion count a first-class,
visible property of every recipe and every calendar entry, and states plainly
when quantities were scaled from the source. Nothing about the scaling maths
changes — this is about the user being able to trust what is already happening.

> **DONE 2026-07-25.** Shipped: `CalendarEntry.servings` / `.source_servings`
> (both `None`-defaulted for legacy localStorage entries) plus the pure
> `servings_are_known` / `servings_were_scaled` helpers in `models/calendar.py`;
> `add_to_calendar` now stamps both counts from `deps.last_recipe` and **prefers
> the resolved recipe's scaled ingredients over the model's retyped argument**
> (guarded by `_same_dish`, so a second dish in the same turn can't inherit
> them); `CalendarAddResult` carries the counts and one prompt rule lets the
> agent confirm them. Frontend: `lib/servings.ts` (`portionsLabel`, shared by
> both surfaces), the counts mapped through the WS boundary in `ChatPanel.tsx`,
> the chat recipe card and calendar modal switched to `portionsLabel`, and a
> compact portion badge on the calendar chip.
>
> Tests: 250 core unit, 91 client, 25 frontend, **6/6 live integration** — all
> green; ruff + pyright clean.
>
> **The live tier earned its keep.** The new 8-person e2e failed on first run:
> the entry came back `servings=4, source_servings=None` carrying the page's own
> `4 piersi z kurczaka`. Root cause was pre-existing and NOT introduced by this
> STEP — `get_recipe_from_url` scaled from `deps.onboarding.servings`, but a
> pasted link never populates it (proposals are skipped, and the prompt tells the
> model not to run onboarding for a link), so `target_servings` was 0 and
> `scale_recipe_to_servings` never ran. The existing 4-person test passed only
> because 4 coincidentally matched the page. Fix: `get_recipe_from_url` takes an
> explicit `servings` argument, persists it to onboarding (the STEP 46 pattern),
> and one prompt rule tells the model to pass it. Pinned hermetically by
> `test_get_recipe_from_url_scales_to_its_servings_argument` so the paid tier is
> no longer the only guard.
>
> Deviation from the plan: `RecipeModal` now takes the whole `CalendarEntry`
> rather than just its `Recipe`, because the entry's counts are authoritative and
> the nested recipe may predate STEP 49. `Recipe.original_servings` was also
> added to the frontend `Recipe` type — the chat card needs it to show the
> scaled-from provenance before an entry exists.

### Current state (audited 2026-07-25)

The maths is done and correct. The **communication** is missing.

Already working:

- `Recipe.servings` + `Recipe.original_servings`
  ([models/recipe.py:29-34](packages/cookbot-core/cookbot/models/recipe.py#L29-L34)) —
  the latter records the source page's own count before any scaling.
- **Ingredients are already scaled to the user's requested servings.**
  `scale_recipe_to_servings` + `RecipeScaleAgent`
  ([agents/recipe_scale.py](packages/cookbot-core/cookbot/agents/recipe_scale.py))
  run in **both** resolve paths —
  `resolve_recipe` ([chat.py:525-535](packages/cookbot-core/cookbot/agents/chat.py#L525-L535))
  and `get_recipe_from_url` ([chat.py:1085-1097](packages/cookbot-core/cookbot/agents/chat.py#L1085-L1097)) —
  and no-op safely when the source count is unknown, already matches, or the
  target is nonsense.
- `propose_recipes` persists `servings` into `deps.onboarding`
  ([chat.py:905-906](packages/cookbot-core/cookbot/agents/chat.py#L905-L906)), the
  STEP 46 fix, so "przepis na X dla 8 osób" reaches the scaler in one turn.
- **The shopping list needs no changes.** `get_shopping_list`
  ([chat.py:1158-1195](packages/cookbot-core/cookbot/agents/chat.py#L1158-L1195))
  and `POST /v1/shopping-list/build` concatenate entry `ingredients` and let the
  ShoppingListAgent dedupe/sum — those lines are *already* scaled.
- A live test pins the no-op case (page serves 4, user wants 4):
  [tests/integration/test_url_servings_calendar_live.py](packages/cookbot-core/tests/integration/test_url_servings_calendar_live.py).

Missing — the trust gap:

- **`CalendarEntry` has no servings field**
  ([models/calendar.py:22-30](packages/cookbot-core/cookbot/models/calendar.py#L22-L30)).
  The count survives only inside the optional nested `recipe` dict, and is absent
  entirely on entries added without a resolved recipe.
- **`add_to_calendar` takes `ingredients` as a model-generated argument**
  ([chat.py:1106-1133](packages/cookbot-core/cookbot/agents/chat.py#L1106-L1133)).
  The LLM retypes the list into the tool call, so it can pass the *pre-scale*
  amounts while `deps.last_recipe` holds the scaled ones. The entry then silently
  disagrees with its own nested recipe — the one defect that can make a displayed
  portion count a lie.
- **Both portion displays are bare numbers with no provenance.** `Porcje: {n}` in
  the chat recipe card ([ChatPanel.tsx:424](frontend/src/components/ChatPanel.tsx#L424))
  and the calendar modal ([CalendarPage.tsx:373](frontend/src/components/CalendarPage.tsx#L373)).
  Neither says whether the amounts were scaled, nor from what. A `0` or a
  defaulted `2` renders as confidently as a real, verified count.
- **The calendar chip shows no portion count at all** — the user must open the
  modal to learn it.
- **No unit test asserts the scale-up path.** `test_recipe_scale.py` exists but
  the "user asks for 8, page serves 4" case is untested at the unit tier.
- No `ui_strings` copy for portions or scaling provenance.

### Design decisions (settled during planning 2026-07-25)

- **This is a visibility feature, not a scaling feature** (the user's correction).
  `RecipeScaleAgent` already adjusts amounts on add-to-calendar. No new scaling
  machinery, no multiplier control, no second source of truth for the number.
- **No multiplier anywhere.** Explicitly dropped from the earlier draft at the
  user's request. If a portion count is unknown the app says so; it does not ask
  the user to correct it by hand.
- **Servings become first-class on `CalendarEntry`** — `servings: int | None` and
  `source_servings: int | None`, mirroring `Recipe.servings` /
  `original_servings`. Both default to `None` so pre-STEP-49 `localStorage`
  entries keep parsing (the STEP 48 `meal_slot` compatibility pattern).
- **The tool reads servings and ingredients from `deps.last_recipe`, never from a
  model argument.** `add_to_calendar` gains no servings parameter. The resolved
  recipe is the authority; retyped tool arguments are the same corruption class
  the pinned-URL fix addressed. Falls back to `deps.onboarding.servings` only
  when there is no resolved recipe. **This is the correctness fix that makes the
  displayed count true** — a label describing a list it doesn't match is worse
  than no label.
- **Provenance is shown, not just the number.** When `source_servings` differs
  from `servings`, the UI states both — `Porcje: 8 (przeliczone z 4)` — so the
  user can see the adjustment happened rather than having to trust it silently.
  When they match, just `Porcje: 4`. When unknown, `Porcje: nieokreślone`.
- **"Unknown" is** `servings is None or servings <= 0`. The rule lives in one pure
  helper, not duplicated across two components and a tool.
- **Both display sites get the same treatment** — the chat recipe card and the
  calendar modal. A count that reads differently in two places is exactly the
  inconsistency that erodes trust.
- **The chip gets a compact portion badge** so a week of meals is readable at a
  glance without opening modals.
- **No prompt changes, no new LLM call, zero per-turn token increase** — STEP 42
  quotas unaffected.
- **No `TenantConfig` field, no env var.** Portion display is fixed product
  behaviour, not a per-tenant tunable.
- **No protocol change.** `WsOutCalendarUpdate` nests the whole `CalendarEntry`
  (verified), so new fields ride along; no `WsMessageType` member added.

### Tasks

- [x] **Core models** — `models/calendar.py`: add `servings: int | None = None`
      and `source_servings: int | None = None` to `CalendarEntry`, commented with
      `None` = unknown and the legacy-entry rationale.
- [x] **Core models** — a pure, I/O-free helper (`models/calendar.py` beside the
      model, `models/quota.py` style): `servings_are_known(servings) -> bool` and
      `servings_were_scaled(servings, source_servings) -> bool`, so the three
      display states are defined once and unit-tested without a UI.
- [x] **Agent/tool** — `agents/chat.py`, `add_to_calendar`: populate `servings` /
      `source_servings` from `ctx.deps.last_recipe.recipe` (fallback
      `deps.onboarding.servings`), and prefer the resolved recipe's scaled
      `ingredients` over the model-supplied argument when they refer to the same
      dish. Add both fields to `CalendarAddResult` so the ChatAgent can mention
      the portion count naturally in its confirmation ("Dodałem na 26.07, 8 porcji").
- [x] **Agent/tool (unplanned — found by the live e2e)** — `get_recipe_from_url`
      takes an explicit `servings: int = 0` argument, persists it to
      `deps.onboarding.servings` and scales from it. A pasted link populates that
      field via neither proposals nor onboarding, so scaling silently never ran.
      One prompt rule tells the model to pass it alongside the URL.
- [x] **Protocol** — none. Confirmed `WsOutCalendarUpdate` passes the new fields
      through unchanged (`test_calendar_update_ws_message_round_trips_servings`).
- [x] **REST API** — none.
- [x] **Env / config** — none.
- [x] **Frontend** — `lib/servings.ts` (new): one `portionsLabel(servings,
      sourceServings, ui)` returning the known / scaled / unknown string, so both
      components and the tests share it.
- [x] **Frontend** — `types.ts`: `servings?: number`, `sourceServings?: number`
      on `CalendarEntry`. Apply `portionsLabel` in the calendar modal **and**
      the chat recipe card. Add a compact portion badge to `RecipeChip` and
      include the count in its `title`. Copy through `ui_strings.py` with Polish
      fallbacks (STEP 48 pattern).
- [x] **Tests:**
  - Core unit `tests/test_agents/test_recipe_scale.py` — **scale-up**: page serves
    4, target 8 → `servings == 8`, `original_servings == 4`, line count preserved,
    name/steps/`source_url` untouched (`TestModel`). Plus unknown-anchor:
    `servings == 0` → no agent call, ingredients verbatim.
  - Core unit `tests/test_agents/test_chat.py` — `add_to_calendar` stamps
    `servings=8` / `source_servings=4` from `deps.last_recipe`; entry ingredients
    come from the resolved recipe, **not** from a divergent model-supplied
    argument; with no `last_recipe`, falls back to onboarding or stays `None`.
    Plus the two `get_recipe_from_url` scaling guards added after the live failure.
  - Core unit **`tests/test_calendar_servings.py` (new file, not `test_models.py`
    as planned)** — `servings_are_known` / `servings_were_scaled` truth tables
    and legacy `CalendarEntry` parsing. Kept separate because `test_models.py` is
    a protocol/session module and this is a self-contained concern.
  - Core unit — the "no second scaling at list-build time" case landed in
    **`test_agents/test_chat.py`** (where `get_shopping_list` lives) rather than
    `test_shopping_list.py`, which is prompt-guards-only and sets
    `ALLOW_MODEL_REQUESTS = False`.
  - Frontend `frontend/src/lib/servings.test.ts` — `node:test` over the three
    label states, incl. `0` and `undefined` both reading as "nieokreślone".
  - Integration (live, extend
    `tests/integration/test_url_servings_calendar_live.py`) — the mirror of the
    existing no-op test: **"dodaj do kalendarza dla 8 osób"** against the same
    4-serving page → `CalendarAddEvent` entry has `servings == 8`,
    `source_servings == 4`, `source_url` preserved, and the chicken count is
    visibly larger than the page's `4 piersi`. **This is the test that caught the
    `get_recipe_from_url` bug.**

### Deferred within this feature

- **Editing portions after an entry is on the calendar** (changing 8→4 in place) —
  needs a re-run of `RecipeScaleAgent` from a non-chat surface. Separate STEP.
- **Per-dish portion attribution in the shopping list** ("8 porcji × curry") — the
  ShoppingListAgent merges across dishes, so tracing a quantity back to one dish
  is a different output shape.
- **Portion counts on proposal cards** — `RecipeSummary` has no `servings` field
  and the zero-LLM fast path (STEP 47) must not gain a model call to invent one.
  A card's portion count is only knowable after resolve.
- **Firestore persistence for the calendar** — still `localStorage`; inherited
  deferral from STEP 48.

### Verify

```bash
cd packages/cookbot-core && uv run pytest -m "not integration" -q
cd clients/tastyhub     && uv run pytest -q
cd frontend             && npm test && npx tsc --noEmit
cd packages/cookbot-core && uv run ruff check . --fix && npx -y pyright@latest

# Live tier (costs money, occasionally flaky — see the module docstring):
cd packages/cookbot-core && uv run pytest -m integration tests/integration/test_url_servings_calendar_live.py -v
```

> Ran 2026-07-25: 250 core unit · 91 client · 25 frontend · **6/6 live** (104s),
> ruff check + pyright clean. `ruff format --check` reports drift on 37 files
> repo-wide, but the same files fail at the parent commit — pre-existing, and the
> CLAUDE.md gate is `ruff check`.

### ⏸ PAUSE 49


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
