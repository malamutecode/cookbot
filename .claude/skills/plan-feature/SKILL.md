---
name: plan-feature
description: Take a feature idea for CookBot from raw one-liner to a concrete, reviewable implementation plan written into TASK.md. Interrogates the idea, audits the current code for what already exists, maps the vertical slice (model → agent/tool → WS protocol → REST → frontend → tests), settles design decisions, and emits a STEP-shaped spec with acceptance criteria. Use when the user says "I want to add", "new feature", "plan a feature", "how would we build", "let's design", or hands over a rough product idea before any code is written.
---

# plan-feature

Turn a feature idea into a **STEP entry in `TASK.md`** that another agent (or you,
later) can implement without re-deriving anything.

The output is a plan, not code. **Write no implementation during this skill** —
the deliverable is the spec plus the user's approval of it. The one exception is
read-only exploration of the repo, which is mandatory.

**Why this repo needs a planning ritual:** CookBot is a strict-layered monorepo
with a one-way dependency rule and 8 hard agent rules. A feature that ignores the
layering (e.g. a tool that sends on the WebSocket directly, or client logic in
core) is expensive to unwind after it's written. Planning catches that for free.

---

## Phase 1 — Interrogate the idea

Ideas arrive underspecified. Before any code reading, resolve these. Ask the user
only what you genuinely cannot infer — batch the questions into **one**
`AskUserQuestion` call rather than a slow interview.

| Question | Why it changes the plan |
|---|---|
| **Who triggers it?** User in chat / a button in the SPA / an admin / background | Chat ⇒ a ChatAgent tool. Button ⇒ a REST route. Admin ⇒ `require_admin`. |
| **Is it conversational or CRUD?** | Conversational ⇒ agent + WS event. CRUD ⇒ plain REST + Firestore, **no agent at all**. |
| **Does it need the LLM?** | If no, do not add an agent. The cheapest feature is one with no model call. |
| **What does the user see?** | Decides whether a new `WsMessageType` and a frontend component are in scope. |
| **Per-user or per-tenant?** | Per-user ⇒ `users/{uid}/…` in Firestore. Per-tenant ⇒ `TenantConfig` field + env var. |
| **What's explicitly out of scope?** | Deferrals get recorded in the STEP so they aren't silently dropped. |

Restate the idea back in two sentences and get agreement before Phase 2. A
misunderstood premise wastes the whole plan.

---

## Phase 2 — Audit what already exists

**Never plan against assumptions.** Half of feature requests here are partly
built already. Find out, then say so.

```bash
# What's the current step, and has this been proposed/deferred before?
grep -n "STEP\|Deferred\|SKIPPED" TASK.md | tail -30

# Does a model / route / component for this already exist?
ls packages/cookbot-core/cookbot/models/ clients/tastyhub/app/api/ frontend/src/components/

# Search for the domain noun before inventing it.
# The --exclude-dir flags are load-bearing: without them .venv/ buries every real hit.
grep -rin "<feature-noun>" packages/ clients/ frontend/src \
  --include=*.py --include=*.tsx -l \
  --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=__pycache__
```

Read the nested `CLAUDE.md` for **every layer the feature will touch** — they are
the binding rules for that directory, and they load on demand:

| Layer | Read |
|---|---|
| Agents / tools | `packages/cookbot-core/cookbot/agents/CLAUDE.md` — the 8 hard rules |
| Client API, Firestore keys, WS | `clients/tastyhub/app/CLAUDE.md` |
| SPA | `frontend/CLAUDE.md` |
| Grocery matching | `packages/delivery-shops/CLAUDE.md` |

Write a short **"Current state (audited YYYY-MM-DD)"** block — what exists, what
doesn't, what's close enough to extend. This goes verbatim into the STEP; it is
the single most useful part of the plan for whoever implements it. STEP 42 in
`TASK.md` is the reference example of this section done right.

---

## Phase 3 — Map the vertical slice

Walk the layers **bottom-up** and decide, for each, *changed / new / untouched*.
Naming a layer "untouched" is a real decision — state it, don't skip it.

1. **Models** (`packages/cookbot-core/cookbot/models/`) — every boundary needs a
   Pydantic model (Architecture Rule 5). Prefer extending an existing model.
   Keep pure math I/O-free in its own module so it's unit-testable — `models/quota.py`
   is the pattern to copy.
2. **Firestore** (`services/firestore.py`) — name the exact document path.
   Per-user data lives under `users/{uid}/…`. **Known trap:** a record written only
   to a subcollection leaves the parent doc non-existent, so a collection `stream()`
   skips it — put user records on the parent doc (see the STEP 42 deviation note).
3. **Agent layer** (`cookbot/agents/`) — only if the LLM is genuinely needed.
   New capability = **a new `@agent.tool` on the ChatAgent**, not a new orchestrator.
   A new sub-agent needs a `build_*_agent(config)` factory + a per-agent
   `TenantConfig.model_*` field. Sub-agent calls must pass `usage=ctx.usage`.
4. **Side effects** — a tool never touches the WebSocket. It appends a `TurnEvent`
   to `ctx.deps.events`; the WS handler drains it via the `_emit_event` match.
   A new event type means: event class + `_emit_event` arm + `WsMessageType` member.
5. **Protocol** (`protocols/ws_messages.py`) — new push to the browser ⇒ a new
   `WsMessageType` value + a `WsOut*` model. Existing values today: `message`,
   `token`, `agent_update`, `hitl_checkpoint`, `hitl_response`, `final_recipe`,
   `recipe_options`, `spizarnia_offer`, `spizarnia_response`, `calendar_update`,
   `shopping_list_update`, `quota_exceeded`, `error`.
6. **REST** (`clients/tastyhub/app/api/`) — one module per resource, mounted in
   `main.py`. Admin surfaces go behind `require_admin`.
7. **Frontend** (`frontend/src/components/`) — a page/panel, plus a NavBar tab if
   it's a new destination. New WS messages must be handled in `ChatPanel.tsx`.
8. **Config** — new tunables become a `TenantConfig` field **and** an env var in
   `settings.py` + `.env.example` + the CLAUDE.md env table. Never hardcode
   tenant values in core.
9. **User-facing copy** — the UI is Polish. Strings belong in
   `models/ui_strings.py`, not inline in components or prompts.

**Check the plan against the architecture rules before writing it down:**

- One-way dependency: `clients/` → `cookbot-core`. Core importing from `clients/`
  is an automatic redesign.
- No client-specific values in core — it goes through `TenantConfig`.
- All state external (Firestore); no module-level session state.
- Async all the way; wrap blocking calls in `asyncio.to_thread()`.
- Tools contain their failures — return a structured failure, never crash the turn.

---

## Phase 4 — Settle the design decisions

List the choices that have more than one defensible answer, and **pick one with a
reason**. This is the section that prevents re-litigation mid-implementation.
Typical axes: storage shape and document key, enforcement/validation point,
sentinel values (`0 = unlimited` is the existing convention), reset/expiry
windows, failure behavior, and which model tier an agent runs on.

If a decision genuinely needs the user, ask now — not after the spec is written.

Also decide **what you are deliberately not doing.** Record deferrals explicitly
in the STEP (STEP 41 deferring the GCS blob cache is the pattern). An unrecorded
deferral reads as an oversight later.

---

## Phase 5 — Write the STEP into TASK.md

Append a new numbered STEP to `TASK.md`, above the Phase 4 / deferred section, and
update the `## Current Step:` line. Match the house format exactly:

```markdown
## STEP NN ★ — <short imperative title>

**Goal:** <2–3 sentences: user-visible outcome and why it matters now.>

### Current state (audited YYYY-MM-DD)
- <what exists today, with file paths>
- <what does NOT exist>

### Design decisions (settled during planning YYYY-MM-DD)
- **<Axis>:** <choice> — <why>

### Tasks
- [ ] **Core models** — `models/x.py`: <models>
- [ ] **Firestore service** — `services/firestore.py`: <methods, doc path>
- [ ] **Agent/tool** — <tool name + which agent>
- [ ] **Protocol** — `WsMessageType.X` + `WsOutX`
- [ ] **REST API** — `app/api/x.py`, routes, auth
- [ ] **Env / config** — `TenantConfig` fields + `.env.example` + CLAUDE.md
- [ ] **Frontend** — component, NavBar tab, WS handling, `ui_strings.py`
- [ ] **Tests:**
  - Core unit `test_x.py` — <cases; `TestModel` for agents>
  - Client unit `test_x.py` — <route/auth cases>
  - Integration (emulator/live) — <only if truly needed>

### Deferred within this feature
- <explicitly out of scope, and why>

### Verify
```
<the exact commands, per the CLAUDE.md test tiers>
```

### ⏸ PAUSE NN
```

Ordering rule: tasks are listed **bottom-up** (models → service → agent →
protocol → REST → frontend → tests) because that's the order they can be
independently tested in.

Every feature ships with tests. Unit tier is hermetic — `TestModel` for agents,
`AsyncMock` for the Firestore service in client tests. Only reach for the
`integration` marker when the emulator or a live LLM is genuinely required.

---

## Phase 6 — Review the plan with the user

Present, concisely:

1. **The vertical slice** — a table of layer → new/changed/untouched.
2. **Design decisions** — each with its one-line rationale.
3. **Risks and unknowns** — what could invalidate the plan.
4. **Estimated blast radius** — which files get touched.

Then ask for approval to write it into `TASK.md` (or, if already written, to
start implementing). **Do not begin implementing in the same turn as approval
unless the user asks** — a plan they haven't read is not a plan.

---

## Gotchas specific to this repo

- **Not every feature needs an agent.** CRUD (spiżarnia, search prefs) is plain
  REST + Firestore. Adding an LLM call where a database query suffices is the most
  common over-engineering here, and it costs tokens on every turn.
- **`propose → resolve` is a two-step flow.** Anything recipe-shaped follows
  `propose_recipes` (cheap summaries) then `get_recipe_details` (full resolve).
  Don't plan a one-shot that resolves four full recipes.
- **Extraction is verbatim; scaling is separate.** Never put serving math into an
  extraction prompt — scale afterwards via `RecipeScaleAgent`.
- **`source_url` is sacred** — provenance must survive any transformation.
- **AI generation is gated** by `allow_ai_generated`; plan the `not_found`
  fallback path, not just the happy path.
- **Onboarding is guided, never a form.** Do not plan anything that blocks tool
  calls until N fields are filled.
- **Token cost is a design axis.** Every turn is metered against per-user daily/
  monthly quotas (STEP 42). A feature that adds an LLM call to every turn shrinks
  every user's budget — say so in the plan.
- **The UI is Polish.** Copy goes in `ui_strings.py`.
- **Deployment is mid-flight** — STEP 27/28 are open. A feature needing new
  infrastructure (a bucket, a secret, a second service) must say how it deploys,
  or be deferred behind them.

## After the plan

Implementation is a separate, explicitly requested act. When it happens, the
`pre-commit-check` skill picks the right test tier from the resulting diff.
