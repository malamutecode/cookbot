---
name: optimize-turn
description: Measure and reduce what a chat turn costs — tokens, LLM calls, page fetches, and perceived latency — against a checked-in baseline. Counts round-trips deterministically rather than timing wall-clock, so it catches regressions the test suite cannot see. Use when a turn feels slow, before/after a performance change, when adding anything to the hot path, or when the user asks to "optimize", "why is this slow", "reduce tokens", or "how much does a turn cost".
---

# optimize-turn

Turn cost is a **product constraint** here, not an engineering nicety. Every turn
is metered against per-user daily and monthly quotas (STEP 42), so an extra LLM
call on the hot path permanently shrinks every user's budget. And `run_stream`
yields nothing until the entire tool chain returns, so a slow turn is a bare
spinner — the part users actually complain about.

This skill measures four things and moves them in the right direction:

| Axis | Instrument that already exists |
|---|---|
| **Tokens per turn** | `ChatAgentDeps.last_turn_total_tokens` (set after the stream, incl. sub-agents) |
| **LLM round-trips** | `UsageLimits(request_limit=...)`, `TenantConfig.usage_request_limit` (25) |
| **Page fetches** | `ChatAgentDeps.page_cache` — per-turn, keyed by URL |
| **Perceived latency** | `ProgressEvent` / `deps.emit_progress()` — is the user told anything? |

---

## Step 0 — The measurement rule

**Count round-trips, never wall-clock.** This is settled in this repo and
`tests/test_agents/test_turn_performance.py` states why: wall-clock "belongs in
the live tier and is far too noisy to gate on — counting round-trips catches the
same regressions deterministically."

So every optimization claim here is expressed as a **count**: N fetches, N model
requests, N tokens. Those are hermetically testable with fakes, stable in CI, and
provably attributable to a diff. A stopwatch on a live run is none of those.

Report a wall-clock number only as supporting color from the live tier, always
labeled as noisy, and never as the thing a test gates on.

---

## Step 1 — Establish where the cost actually is

Do not optimize from intuition. The expensive turns in this product are known:

| Turn shape | Why it's expensive |
|---|---|
| **Pasted URL → recipe card** | fetch page → extract → split cross-check → scale. The single slowest common turn; one live page measured at ~238k chars before cleaning. |
| **`propose_recipes` (LLM path)** | web search + `RecipeOptionsAgent` over N results |
| **Full onboarding → proposals** | several chat turns, each a model call |
| **`pick_recipe` click** | should be **zero** LLM turns — a click is data, not a sentence |

Read the hot path before changing it:

```bash
grep -n "page_cache\|emit_progress\|usage=" packages/cookbot-core/cookbot/agents/chat.py | head -40
```

The two optimizations already landed (commit `f01fead`) are the pattern to
extend, not to redo:

1. **Fetch a page once per turn** — `deps.page_cache` deduplicates the extractor's
   `web_fetch` and the STEP 45 split cross-check, which read the same URL
   microseconds apart. Deliberately per-turn, not per-connection: pages change,
   connections are long-lived, and page text must never enter the Firestore
   snapshot.
2. **Emit progress during slow tools** — `deps.emit_progress()`, drained
   incrementally by the WS handler via the `progress_sent` cursor.

---

## Step 2 — Measure the baseline before changing anything

```bash
cd packages/cookbot-core && timeout 90 uv run pytest tests/test_agents/test_turn_performance.py -q
```

These are **unit** tests — hermetic, no network, no LLM. They assert the *shape*
of a turn's work using fake agents that count their own calls. Extend this file
rather than starting a new harness; the `_FakeAgent` / `_FakeRun` pattern in it is
the established way to count round-trips here.

For token counts, which need a real model, take the number from a live run:
`stream_chat_response` writes `deps.last_turn_total_tokens` after the stream. Run
the relevant live test and record the value per turn shape. Mark these numbers
with the model that produced them — a token count without its model is noise, and
tastyhub overrides `model_web_search` to `gpt-4o-mini`.

---

## Step 3 — Optimize, in this order

Cheapest and safest first. Stop as soon as the turn is acceptable — an
optimization nobody needed is complexity nobody asked for.

1. **Delete work that is duplicated.** Two calls doing the same fetch, parse, or
   extraction. Zero behavior risk. `page_cache` was this.
2. **Delete work that is unnecessary.** A model call where a heuristic or a
   database read suffices — the most common over-engineering in this repo, per
   `plan-feature`. The `recipe_search_fast.py` zero-LLM path (STEP 47) is the
   reference: plain "przepis na X" takes a `DDGS()` fast path with **no** model call.
3. **Parallelize independent I/O.** Several fetches or sub-agent calls that don't
   depend on each other → `asyncio.gather`. Watch the failure semantics: commit
   `88dad3c` (`stop one slow host discarding every card's image`) is exactly the
   bug this introduces — one slow member must not sink the batch. Bound it with
   a per-item timeout and degrade that item, not the set.
4. **Shrink the payload.** Truncation is a *hazard* here, not a free win: the
   memory `fetch-truncation-caused-fabrication` records that clipping page content
   makes the extractor invent ingredients. If you touch `_MAX_PAGE_CONTENT` or any
   truncation bound, verify the ingredient list still survives — and prefer
   smarter cleaning over a smaller cap.
5. **Move to a cheaper model.** Last resort, and never silently. This is a quality
   trade-off, so it goes through `eval-agent` for a scored before/after, not a
   vibe check.
6. **Hide the cost you cannot remove.** If the work is genuinely required, emit
   progress so the user isn't watching a dead spinner. Perceived latency is a real
   axis, not a consolation prize.

**Never** cache across turns to make a number look better. Pages change; a
connection is long-lived; stale recipe content is a correctness bug that presents
as a mystery. Per-turn is the boundary.

---

## Step 4 — Lock the win in with a counting test

An optimization without a regression guard gets undone by the next refactor.
Every change from Step 3 ships with a test in `test_turn_performance.py` that
asserts the **count**, using the fake-agent pattern already there:

```python
assert fetches == 1, "the page was downloaded more than once in a single turn"
assert fake_agent.calls == 0, "a click must resolve with no LLM turn"
```

Write the assertion message so it explains the *property*, not the number — the
next reader needs to know why 1 is correct, not just that it is.

Token budgets are the exception: they are model-dependent and drift legitimately,
so record them in a baseline note rather than a hard assert, and check them in
`eval-agent` runs where the model is pinned.

---

## Step 5 — Report

Give a before/after table of counts, with the diff that caused each move:

```
page fetches (URL turn)   2 → 1    page_cache dedupes the split cross-check
LLM requests (click)      1 → 0    pick_recipe resolves from state
tokens (URL turn, mini)  ~8.4k → ~8.4k   unchanged
progress events           0 → 3    fetch / extract / scale
```

State plainly what did **not** improve, and what you traded. If a change cut
tokens but risks quality, say so and route it through `eval-agent` before
recommending it ships — a cheaper turn that answers worse is not an optimization.

---

## Interaction with the other skills

- **`pre-commit-check`** runs the unit tier that includes
  `test_turn_performance.py` — your counting tests are enforced there
  automatically, for free, on every Python change.
- **`eval-agent`** owns the quality half. Any optimization touching a model
  choice, a prompt, or a truncation bound must be scored there, because this
  skill measures cost and is blind to whether the answer got worse.
- **`plan-feature`** already treats token cost as a design axis. When a *new*
  feature adds a call to the hot path, that belongs in the plan — not in a
  cleanup pass afterwards.
