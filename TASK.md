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
Firebase Hosting frontend, scripted in `infra/`). Steps 1–50 are shipped; their
history is in `git log` and their behaviour is documented in the `CLAUDE.md` files.

One thing remains open:

| Step | What | Size |
|---|---|---|
| [STEP 45](#step-45--multi-recipe-pages-ask-whether-to-split) ★ | Multi-recipe pages: ask whether to split | the real feature work |

---

## STEP 45 ★ — Multi-recipe pages: ask whether to split

**Goal:** When a fetched page contains more than one recipe, stop silently merging
them. Ask the user whether to keep them as one recipe or split them into separate
ones — unless the extra block is a small *component* (a sauce, a dressing, a
marinade), which should stay folded in without asking.

### Why (found live, 2026-07-25)

The chilitonka curry+naan page hosts **two independent recipes** under one URL: the
curry ("Składniki dla 4 osób") and naan bread ("Składniki na 8 porcji"). Extraction
is faithful per agents/CLAUDE.md Rule 5, so it correctly captures everything — and
returns a single `Recipe` with **21 ingredients and 17 steps** whose `servings=4`.

Two things then go wrong downstream:

- **The shopping list is wrong.** A 4-person curry request also buys 8 portions of
  naan (500 g flour, 110 g butter, yeast) with no indication why.
- **`servings` is ambiguous.** One integer cannot describe "curry for 4 + bread for
  8", so scaling to a different count silently rescales both by the curry's ratio.

> This is **not** an extraction bug. Do not "fix" it by teaching the extractor to
> drop the second recipe — the page really does contain both. The product question
> is what the user wants, which only the user can answer.

The page is already pinned by `tests/integration/test_url_servings_calendar_live.py`
and referenced from `chat.py` / `web_search.py` / `test_chat.py`.

### Design decisions to settle before coding

- **Component vs. standalone.** A second ingredient block is a *component* (fold in
  silently) when it has no serving count of its own, or its count matches the main
  recipe, or its heading names a part of the dish
  (sos/dressing/marynata/polewa/krem/farsz). It is *standalone* (ask) when it has
  its own distinct serving count — as naan does with "na 8 porcji" — and reads as a
  dish that could be cooked alone. Prefer this over an ingredient-count threshold:
  "8 porcji" is the signal that actually distinguished the two blocks here.
- **Where the split decision lives.** Extraction must stay verbatim, so the
  extractor only *reports* the blocks it saw; the ChatAgent decides whether to ask.
  Likely shape: the fetch agent gains an optional `components: list[RecipeComponent]`
  (name + servings + ingredients + steps), and a ChatAgent tool asks when >1
  standalone block came back. Confirm against agents/CLAUDE.md Rule 1 (new
  capability = a ChatAgent tool) before implementing.
- **How the question reaches the user.** There is no generic "ask the user a
  question" event today — the ChatAgent asks in prose and reads the next turn's
  reply. Decide whether that suffices (cheapest, matches guided onboarding) or
  whether this needs a typed choice event plus a frontend affordance. The reply
  must survive a reconnect: whatever holds the pending question belongs in the
  `ChatState` snapshot (Architecture Rule 3), never a module global.
- **What "split" produces.** Two `FinalRecipeEvent`s / two calendar entries, or one
  primary recipe plus a linked side? This decides whether `add_to_calendar` must
  handle a set. Both keep the same `source_url` (Rule 5).

### Acceptance criteria

- [ ] A page with one recipe behaves exactly as today — **no question asked**, and
      no extra LLM call on the common path.
- [ ] A page whose second block is a sauce/dressing (no own serving count) folds
      into the main recipe silently, as today.
- [ ] The chilitonka curry+naan page asks the user which they want.
- [ ] Choosing "split" yields a curry recipe with `servings=4` whose ingredients
      contain no flour/yeast, and a separate naan recipe with `servings=8`.
- [ ] Choosing "keep together" reproduces today's merged behaviour.
- [ ] The shopping list for a 4-person curry contains no naan ingredients once split.
- [ ] `source_url` is preserved on every recipe produced (Rule 5).
- [ ] Unit tests with `TestModel` for the component-vs-standalone heuristic — it is
      a pure function, so test it directly, no LLM.
- [ ] Live e2e extending `tests/integration/test_url_servings_calendar_live.py`,
      which currently asserts the merged 21-ingredient behaviour — **update those
      assertions in the same commit.**

### Verify

```bash
cd packages/cookbot-core && uv run pytest -m "not integration" -q
cd clients/tastyhub     && uv run pytest -q
cd frontend             && npm test && npx tsc --noEmit
uv run ruff check . && uv run python ../../tools/check_pyright.py   # per package

# Live tier (costs money, occasionally flaky):
cd packages/cookbot-core && uv run pytest -m integration tests/integration/test_url_servings_calendar_live.py -q
```

### ⏸ PAUSE 45

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

- **Firestore persistence for the calendar** — it is `localStorage` only, so a meal
  plan does not follow the user across devices. Needs a document shape, a
  sync/conflict story and a migration.
- **Editing portions after an entry is on the calendar** (8→4 in place) — needs
  `RecipeScaleAgent` re-run from a non-chat surface.
- **Frisco add-to-basket / logged-in cart / substitutes** — gated on the licensing
  blocker in
  [packages/delivery-shops/CLAUDE.md](packages/delivery-shops/CLAUDE.md), which
  also records the verified API facts.
- **Emailing the temp password** (admin-created users) — chosen against while no
  email sender is configured; additive when it lands.
- **Audit log of admin actions** — worth doing before this is multi-admin in prod.
- **Cascade delete of user subcollections** — `DELETE /v1/admin/users/{uid}` leaves
  `spizarnia` / `prefs` / usage counters as orphans. Harmless today but it is
  silent data retention after account deletion. See the docstring on
  `FirestoreService.delete_user_record`.
- **Caching DDG results per dish** — needs Firestore/GCS; belongs with the blob-cache work.
- **Streaming enrichment on the fast path** — needs a new `WsMessageType` plus
  merge-by-index in `ChatPanel`, and risks reintroducing an LLM call.
