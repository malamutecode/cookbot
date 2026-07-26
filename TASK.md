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

**Current Step: none — pick the next one.**

---

## Known defect — split-answer turn falls into the fast path

`○` **`test_answering_split_yields_a_curry_without_naan_ingredients` fails live**
(`tests/integration/test_url_servings_calendar_live.py`). Not flakiness and not
caused by the pick-recipe fix: **verified by stashing that change and reproducing
the identical failure on a clean tree.**

- **Symptom:** 0 `FinalRecipeEvent` cards instead of 2 after the user answers the
  split question. The assertion is `expected two recipe cards after splitting, got 0`.
- **Mechanism:** extraction and the split question both work — the logs show
  `split_retry_recovered standalone=['Naan bread']`, so the question is asked
  correctly. The failure is one step later: answering "Rozdziel je na osobne
  przepisy" routes into the **zero-LLM DuckDuckGo proposal fast path** (searching
  `"Kurczak w kremowym sosie curry na ostro przepis"`) instead of emitting the two
  cards from the content already sitting in `deps.pending_split`. The reply lists
  fresh search results and asks the user to pick one.
- **Why this matters:** this is exactly the regression the `pending_split` prompt
  branch was added to prevent (see "Multi-recipe pages" in
  [agents/CLAUDE.md](packages/cookbot-core/cookbot/agents/CLAUDE.md)) — the model
  reaching for its most familiar tool and discarding the page it already has. The
  branch *is* ordered first and does fire; the answer turn escapes it anyway, so
  the guard is incomplete rather than missing.
- **Note the docs are wrong about this test.** The flaky-test note in
  agents/CLAUDE.md describes an *extraction* miss ("couldn't read the page"). That
  is a different failure mode from this one. An earlier run did show the
  extraction-miss variant, so the test likely has two distinct failure paths —
  don't assume the documented one when triaging.
- **Start at:** `onboarding_status_prompt`'s `pending_split` branch and the
  `choose_recipe_split` tool in `cookbot/agents/chat.py`; check whether the fast
  path in `propose_recipes` is reachable while `deps.pending_split` is set (it
  should be refused outright).

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
