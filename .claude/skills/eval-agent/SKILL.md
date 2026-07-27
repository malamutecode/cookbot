---
name: eval-agent
description: Score agent/prompt changes against a graded case set instead of pass/fail asserts. Runs the eval corpus in tests/eval/, compares the score to the checked-in baseline, separates real regressions from live-web flake by re-running only the failures, and reports a per-case diff. Use when changing a prompt or agent instructions, choosing a model tier, before/after an agent refactor, or when the user asks to "eval", "score the agent", "did this prompt make it better", or "is this regression or flake".
---

# eval-agent

Answer one question the test suite cannot: **did this change make the agent better
or worse?**

`pre-commit-check` proves the code runs. The live tier in `tests/integration/`
proves the pipeline is wired. Neither scores *quality* — their assertions are
deliberately loose (`"assertions target structure and the web split, not an exact
4"`) because live web search is flaky. A loose assert cannot detect a prompt edit
that degrades extraction by 20%; it only fires when something breaks outright.

This skill closes that gap with a **graded corpus + a checked-in baseline score**.

**Why this repo needs it:** 8 of the last 60 commits were agent-behavior
regressions found after shipping — `reject yield weights masquerading as serving
counts`, `resolve the recipe card the user actually clicked`, `refuse recipe
searches while a split question is pending`. Each was fixed by adding one assert
for that one case. That is ratchet-by-anecdote: it prevents the exact bug from
returning and says nothing about the next one. A scored corpus generalises.

---

## Step 0 — Is an eval the right tool right now?

Run this skill when the diff touches **agent behavior**: an `instructions=` block,
a prompt builder (`*_prompt`), a `@agent.tool` docstring (the model reads it), a
`build_*_agent` factory, a `TenantConfig.model_*` value, or extraction/parsing
heuristics in `web_search.py` / `recipe_search_fast.py`.

Do **not** run it for pure plumbing — a Firestore method, a REST route, a
frontend component. Those cost money here and learn nothing.

It costs real OpenAI spend and takes minutes. **Always confirm with the user
before the first scored run**, and say roughly how many LLM calls it implies.

---

## Step 1 — Locate or bootstrap the corpus

The corpus lives at `packages/cookbot-core/tests/eval/`:

```
tests/eval/
├── cases/
│   ├── extraction.yaml      # URL → expected recipe facts
│   ├── routing.yaml         # utterance → expected tool + state
│   └── scaling.yaml         # recipe + target servings → expected quantities
├── baseline.json            # last accepted score, per suite
└── run_eval.py              # runner: scores cases, diffs vs baseline
```

If `tests/eval/` does not exist yet, **bootstrap it before evaluating anything**
— see Step 6. Do not invent an ad-hoc one-off script; the value is entirely in
the corpus persisting across changes.

---

## Step 2 — Seed cases from real failures, never from imagination

This is the rule that makes the corpus worth its cost. A case earns its place by
being a **failure that actually happened** or a **contract the docs state**.

Three legitimate sources, in priority order:

1. **Shipped regression fixes** — mine them:
   ```bash
   git log --oneline -80 | grep -E "fix\((chat|agents|fast-path|web-search)\)"
   ```
   Each of these has a known-wrong old behavior and a known-right new one. That
   is a perfect graded case. Read the commit body for the input that triggered it.

2. **The hard rules** in `packages/cookbot-core/cookbot/agents/CLAUDE.md` — each
   is a falsifiable claim and deserves at least one case:
   - source_url survives scaling → assert `source_url` unchanged after a servings change
   - extraction is verbatim → assert extracted quantities equal the page's, unscaled
   - a click is data, not a sentence → assert `pick_recipe` resolves with zero LLM turns
   - AI generation is gated → assert `allow_ai_generated=False` yields `not_found`

3. **User-reported misbehavior** — when the user describes a bad turn, add it as a
   case *in the same session*, before fixing it. The case then proves the fix.

Each case is a dict with an input, a set of graded assertions, and a weight:

```yaml
- id: yield-weight-not-servings
  origin: dce3c25            # the commit that made this a known failure
  url: https://example.com/przepis/ciasto
  expect:
    servings_between: [6, 12]   # "1.2 kg" must NOT be read as 1200 servings
    source_url_preserved: true
    min_ingredients: 4
  weight: 2                     # a rule violation counts double
```

**Graded, not boolean.** A case yields a 0.0–1.0 score, so partial degradation is
visible. `min_ingredients: 4` returning 3 scores 0.75, not "FAIL" — that is the
signal a loose assert throws away.

---

## Step 3 — Run the corpus

```bash
cd packages/cookbot-core
timeout 600 uv run python tests/eval/run_eval.py --suite all
```

Reuse the existing live-tier plumbing rather than rebuilding it:

- The `pl_config` fixture pattern in `tests/integration/conftest.py` — a
  self-contained Polish `TenantConfig`, no client `.env` dependency.
- Its `_load_env_key_if_missing()` trick: `OPENAI_API_KEY` is read from
  `clients/tastyhub/.env` when not exported.
- Default the heavy agents to `gpt-4o-mini` (TPM headroom), overridable with the
  `INTEG_MODEL_*` env vars. **Eval on the model production actually runs** —
  tastyhub overrides `model_web_search` to mini, so evaluating on `gpt-4o` would
  score a config no user ever hits.
- `pytest-xdist` is already a dev dependency; the live tier went 349s → 89s with
  it. Parallelise the runner the same way.

---

## Step 4 — Separate regression from flake (the load-bearing step)

Live web search is flaky, so **a single failing case proves nothing.** Never
report a regression from one run.

When a case fails, re-run **only the failures**, 3 times:

```bash
timeout 600 uv run python tests/eval/run_eval.py --only <case-id> --repeat 3
```

Then classify:

| Pattern across 3 re-runs | Verdict |
|---|---|
| Fails all 3 | **Real regression** — the change caused it |
| Fails 1–2 of 3 | **Flaky** — record the flake rate, do not gate on it |
| Passes all 3 | **Transient** — original failure was network noise; ignore |

For a suspected regression, confirm attribution before blaming the diff: stash
the change, re-run that case on the parent commit, and compare. A case that fails
on `HEAD~1` too is a pre-existing bug this change merely revealed — say so.

Track a per-case `flake_rate` in `baseline.json`. A case that flakes above ~30%
is a bad case: either tighten what it asserts (structure, not exact strings) or
pin it to a stable fixture URL. Delete cases that cannot be made stable — a noisy
case trains everyone to ignore the whole suite.

---

## Step 5 — Diff against the baseline, then decide

The runner prints per-suite scores against `baseline.json`:

```
extraction   0.91 → 0.88   ▼ -0.03   (2 cases down, 1 up)
routing      0.95 → 0.95   =
scaling      0.87 → 0.94   ▲ +0.07
```

Interpretation, and what to do:

- **Score dropped beyond flake noise** → report it and **stop**. Show the specific
  cases that regressed with their inputs and actual outputs. Do not update the
  baseline to make it green — that is the one move that destroys the instrument.
- **Score improved** → update `baseline.json` in the same commit, and say which
  cases moved. An improvement nobody records is one somebody re-loses.
- **Score flat** → the change is behavior-neutral. Worth stating plainly; that is
  often exactly what a refactor should score.

Trade-offs are real and the user decides them: a prompt that fixes extraction but
costs routing accuracy is a judgment call, not an automatic reject. Present both
numbers and recommend, don't silently pick.

---

## Step 6 — Bootstrapping the corpus (first run only)

If `tests/eval/` does not exist, build it in this order and **keep it small**:

1. `run_eval.py` — loads YAML cases, runs each through the real agent entry point
   (`resolve_recipe`, `propose_recipes`, or a full `run_chat_turn`), scores the
   `expect` block, writes JSON. Mark it `integration` so the default unit run
   never picks it up.
2. **5–8 cases total**, seeded from the `fix(chat|agents|fast-path)` commits in
   Step 2. Resist a large first corpus — an unmaintained 50-case suite is worse
   than a trusted 6-case one.
3. `baseline.json` from the first clean run, committed with a note on which model
   and date produced it. A baseline without its model recorded is meaningless.
4. A short `tests/eval/README.md`: how to add a case, how to re-baseline.

Cases are Pydantic-modelled at the boundary like everything else here
(Architecture Rule 5) — the YAML loads into a `EvalCase` model, not a raw dict.

---

## Reporting

Report a table: suite → baseline → new → delta → verdict. Then, for anything that
moved, the case id, its input, expected vs. actual, and the flake classification
from Step 4. Close with an explicit recommendation: ship / investigate / revert.

**Never** update `baseline.json` on a regression, and never report a score from a
run whose failures were not re-run per Step 4.
