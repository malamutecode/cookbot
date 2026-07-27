"""LIVE A/B benchmark for `MODEL_CHAT` — STEP 53.

Run with:
    # default pair (gpt-4o-mini vs gpt-5.4-mini), 2 repeats
    BENCH_MODEL_CHAT=1 uv run pytest -m integration \
        tests/integration/test_model_chat_bench_live.py -v -s

    # pick the models and the sample size
    BENCH_MODEL_CHAT=1 BENCH_CHAT_MODELS=gpt-4o-mini,gpt-4o,gpt-5-mini BENCH_REPEATS=4 \
        uv run pytest -m integration tests/integration/test_model_chat_bench_live.py -v -s

Opt-in (`BENCH_MODEL_CHAT=1`) because it spends real tokens: a full sweep is
len(models) x len(CASES) x BENCH_REPEATS live turns.

## What this measures, and why these numbers

The ChatAgent sits on the critical path TWICE in a recipe turn — once to route to
a tool, once to narrate the result — while every other agent runs at most once.
So its per-call latency is doubled in exactly the turn users complain about. That
makes "cheap model for the orchestrator" worth measuring rather than assuming.

Four numbers per model, over the same fixed prompt set:

  TTFT      time to the first streamed token. This is what the user actually
            experiences, and it is the honest number even though mid-turn
            progress events changed what they SEE during that window (not its
            length).
  wall      total turn wall-clock, first token to last.
  tokens    whole-turn `RunUsage.total_tokens` — sub-agents aggregate in via
            `usage=ctx.usage`, so this is the real per-turn cost. Reported
            alongside latency because STEP 42 meters per-user token budgets: a
            faster-but-pricier model changes quota economics.
  misroute  the case's expected side-effect did not appear. This is the number
            most likely to overturn "bigger model = slower": a mis-route costs a
            full extra round-trip plus a re-ask, the worst latency event in the
            product, so a model 200ms slower per call that never mis-routes is
            faster in practice.

            It catches BOTH directions, and both were observed live: gpt-4o
            skipping `update_onboarding` while replying plausibly, and gpt-5-mini
            firing `propose_recipes` on a question that wanted no search. Neither
            is visible in the reply text — hence `chat_only`, whose check is "no
            events at all", not "the prose looks right".

## Why the cases are shaped like this

Only `model_chat` varies; every other agent is pinned to gpt-4o-mini (the
`pl_config` default) so the delta is attributable. The cases deliberately span
the three routing decisions the orchestrator actually makes, WITHOUT dragging in
the expensive extraction path:

  - `direct_dish`  → proposals via the zero-LLM fast path. The ChatAgent's own
                     routing is nearly the entire cost here, so it is the
                     cleanest signal in the set.
  - `onboarding`   → a tool call that only mutates state (`update_onboarding`).
  - `chat_only`    → a cooking question answered with NO tool call. Isolates
                     raw generation speed from routing.

`get_recipe_details` / `get_recipe_from_url` are excluded on purpose: extraction
is ~27s and ~10k tokens (see STEP 54), which would swamp a few-hundred-ms
orchestrator difference in noise. This benchmark answers "which model should
route", not "how long does extraction take".

## Reading the output

The per-model table prints median AND mean. Prefer the median: the live tier is
flaky by nature (real DDG + OpenAI) and one 429-retry or one slow recipe host
drags a mean around. A single repeat proves nothing about a sub-second
difference — raise BENCH_REPEATS before drawing a conclusion.

The `MISROUTES` column outranks the timing columns. A model that is faster per
call but mis-routes at any noticeable rate is the slower model in production.
"""
from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from cookbot.agents.chat import (
    ChatAgentDeps,
    OnboardingState,
    RecipeOptionsEvent,
    build_chat_agent,
    stream_chat_response,
)
from cookbot.models.tenant import TenantConfig

pytestmark = pytest.mark.integration

# Models to compare by default: the incumbent vs the one measured alternative
# that actually tied it on routing (see agents/CLAUDE.md for the numbers).
# Overridable so more can be swept without editing the file.
#
# `gpt-4o` and `gpt-5-mini` are deliberately NOT the default pair. Both were
# measured and both mis-route — in opposite directions, which is the interesting
# part: gpt-4o skips tools it should call, gpt-5-mini calls ones it shouldn't.
# Re-run them with BENCH_CHAT_MODELS if you want to see it.
_DEFAULT_MODELS = ["gpt-4o-mini", "gpt-5.4-mini"]

# Repeats per (model, case). The live tier is noisy; averaging several runs is
# the only way a few-hundred-ms difference means anything.
_DEFAULT_REPEATS = 2

# Retry budget for OpenAI 429s (TPM cap). A rate-limited turn is transient infra,
# not a latency result — the retry sleep is excluded from the timing by
# restarting the clock, and the turn is re-run rather than recorded.
_RETRIES = 3


def _p(line: str = "") -> None:
    """Print, surviving a legacy console codepage (see test_fast_path_live)."""
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(line.encode(enc, errors="replace").decode(enc, errors="replace"))


# ── The fixed prompt set ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Case:
    """One routing decision, with a mechanical check that it routed correctly.

    `check` returns True when the turn did what it was supposed to. It reads
    events/state — never the reply text — because asserting on wording would
    measure the model's prose, not its routing.
    """

    name: str
    message: str
    check: Callable[[str, list[object], ChatAgentDeps], bool]
    why: str


def _routed_to_proposals(reply: str, events: list[object], deps: ChatAgentDeps) -> bool:
    """A named dish must produce proposal cards on this very turn."""
    return any(isinstance(e, RecipeOptionsEvent) and e.proposals for e in events)


def _recorded_onboarding(reply: str, events: list[object], deps: ChatAgentDeps) -> bool:
    """The stated servings must land in onboarding — the scaling anchor."""
    return deps.onboarding.servings == 4


def _answered_without_tools(reply: str, events: list[object], deps: ChatAgentDeps) -> bool:
    """A general cooking question is answered directly: prose, no side-effects.

    Non-empty reply AND no events. A search fired for a substitution question is
    exactly the mis-route this column exists to count.
    """
    return bool(reply.strip()) and not events


CASES: list[Case] = [
    Case(
        name="direct_dish",
        message="Przepis na jagodzianki",
        check=_routed_to_proposals,
        why="route to propose_recipes; zero-LLM fast path, so this is mostly ChatAgent time",
    ),
    Case(
        name="onboarding",
        message="Gotuję dla 4 osób",
        check=_recorded_onboarding,
        why="route to update_onboarding; state-only tool, no sub-agent",
    ),
    Case(
        name="chat_only",
        message="Czym mogę zastąpić masło w cieście?",
        check=_answered_without_tools,
        why="answer directly, NO tool call — isolates generation from routing",
    ),
]


# ── Measurement ───────────────────────────────────────────────────────────────

@dataclass
class Sample:
    case: str
    ttft: float
    wall: float
    tokens: int
    routed_ok: bool
    reply: str = ""


@dataclass
class ModelResult:
    model: str
    samples: list[Sample] = field(default_factory=list)

    @property
    def misroutes(self) -> int:
        return sum(1 for s in self.samples if not s.routed_ok)

    def _col(self, attr: str) -> list[float]:
        return [float(getattr(s, attr)) for s in self.samples]

    def median(self, attr: str) -> float:
        vals = self._col(attr)
        return statistics.median(vals) if vals else 0.0

    def mean(self, attr: str) -> float:
        vals = self._col(attr)
        return statistics.fmean(vals) if vals else 0.0


def _config_with_chat_model(base: TenantConfig, model: str) -> TenantConfig:
    """Clone the tenant config changing ONLY `model_chat`.

    `TenantConfig` already carries per-agent model fields, so the experiment needs
    no code change — which is the point of STEP 53 staying a config-level
    experiment. Every other agent keeps the base value so the delta is
    attributable to the orchestrator alone.
    """
    return replace(base, model_chat=model)


async def _measure_turn(agent, deps: ChatAgentDeps, history: list, text: str) -> Sample | None:
    """Run one turn, returning its timings — or None if it could not be measured.

    TTFT is taken at the FIRST yielded token, which is the earliest moment the
    widget can render anything: `agent.run_stream` emits nothing until the whole
    tool chain returns, so on a tool-calling turn this includes the tool work.
    That is deliberate — it is the number the user lives with.
    """
    for attempt in range(_RETRIES):
        deps.reset_turn()
        try:
            t0 = time.perf_counter()
            ttft: float | None = None
            reply = ""
            async with stream_chat_response(agent, deps, history, text) as tokens:
                async for tok in tokens:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    reply += tok
            wall = time.perf_counter() - t0
        except ModelHTTPError as e:
            if e.status_code == 429 and attempt < _RETRIES - 1:
                await asyncio.sleep(2**attempt * 5)  # 5s, 10s — then give up
                continue
            raise
        if ttft is None:
            # An empty stream has no TTFT to report; recording 0 would silently
            # flatter the model. Drop the sample instead.
            return None
        return Sample(
            case="",
            ttft=ttft,
            wall=wall,
            tokens=deps.last_turn_total_tokens,
            routed_ok=False,
            reply=reply,
        )
    return None


async def _run_model(base: TenantConfig, model: str, repeats: int) -> ModelResult:
    """Sweep every case `repeats` times against one `model_chat` value."""
    config = _config_with_chat_model(base, model)
    result = ModelResult(model=model)

    for rep in range(repeats):
        for case in CASES:
            # A FRESH agent + deps + history per sample. Sharing history would
            # make later cases cheaper than earlier ones (prefix reuse, and the
            # model answering from context instead of routing), so the samples
            # would not be comparable across cases or across repeats.
            agent = build_chat_agent(config)
            deps = ChatAgentDeps(
                config=config,
                onboarding=OnboardingState(),
                preferred_sites=["kwestiasmaku.com", "aniagotuje.pl"],
                allow_ai_generated=True,
            )
            history: list = []

            sample = await _measure_turn(agent, deps, history, case.message)
            if sample is None:
                _p(f"  ! {model} / {case.name} rep{rep + 1}: no tokens streamed — sample dropped")
                continue

            sample.case = case.name
            sample.routed_ok = case.check(sample.reply, list(deps.events), deps)
            result.samples.append(sample)

            flag = "ok " if sample.routed_ok else "MIS"
            _p(
                f"  {flag} {model:<14} {case.name:<12} rep{rep + 1}  "
                f"ttft {sample.ttft:5.2f}s  wall {sample.wall:5.2f}s  "
                f"{sample.tokens:>6,} tok"
            )
    return result


def _report(results: list[ModelResult]) -> None:
    _p()
    _p("=" * 78)
    _p("MODEL_CHAT BENCHMARK (STEP 53) — only model_chat varies")
    _p(f"  cases    {', '.join(c.name for c in CASES)}")
    _p(f"  samples  {min(len(r.samples) for r in results)}+ per model")
    _p("-" * 78)
    _p(f"  {'model':<16}{'TTFT med':>10}{'TTFT mean':>11}{'wall med':>10}"
       f"{'tok med':>10}{'misroutes':>11}")
    for r in results:
        _p(
            f"  {r.model:<16}{r.median('ttft'):>9.2f}s{r.mean('ttft'):>10.2f}s"
            f"{r.median('wall'):>9.2f}s{r.median('tokens'):>10,.0f}"
            f"{r.misroutes:>7}/{len(r.samples):<3}"
        )
    _p("-" * 78)

    # Per-case breakdown: the aggregate hides that `chat_only` is pure generation
    # while `direct_dish` is dominated by DuckDuckGo. Comparing like with like is
    # the only way to see where a model actually differs.
    for case in CASES:
        _p(f"  {case.name}  — {case.why}")
        for r in results:
            rows = [s for s in r.samples if s.case == case.name]
            if not rows:
                _p(f"      {r.model:<16} (no samples)")
                continue
            ttfts = [s.ttft for s in rows]
            walls = [s.wall for s in rows]
            toks = [s.tokens for s in rows]
            mis = sum(1 for s in rows if not s.routed_ok)
            _p(
                f"      {r.model:<16}ttft {statistics.median(ttfts):5.2f}s  "
                f"wall {statistics.median(walls):5.2f}s  "
                f"{statistics.median(toks):>6,.0f} tok  "
                f"misroutes {mis}/{len(rows)}"
            )
    _p("=" * 78)
    _p("  Read the MISROUTE column first: a mis-route costs a full extra")
    _p("  round-trip plus a re-ask, which outweighs a sub-second per-call win.")
    _p("  Prefer the median — one 429 or one slow host skews the mean.")
    _p("=" * 78)
    _p()


@pytest.mark.skipif(
    os.getenv("BENCH_MODEL_CHAT") != "1",
    reason="set BENCH_MODEL_CHAT=1 to benchmark MODEL_CHAT (spends OpenAI tokens)",
)
async def test_model_chat_comparison(pl_config: TenantConfig) -> None:
    """Sweep MODEL_CHAT over the fixed prompt set and print the comparison.

    Deliberately NOT asserted against a wall-clock ceiling: the numbers are the
    deliverable, and any bound tight enough to be meaningful over live DDG +
    OpenAI would be flaky. What IS asserted is that the sweep produced usable
    data — a benchmark that silently measured nothing is worse than none.
    """
    models = [
        m.strip()
        for m in os.getenv("BENCH_CHAT_MODELS", ",".join(_DEFAULT_MODELS)).split(",")
        if m.strip()
    ]
    repeats = int(os.getenv("BENCH_REPEATS", str(_DEFAULT_REPEATS)))
    assert len(models) >= 2, (
        f"need at least two models to compare, got {models!r} — "
        "set BENCH_CHAT_MODELS=a,b"
    )

    _p()
    _p(f"sweeping {models} x {len(CASES)} cases x {repeats} repeats "
       f"= {len(models) * len(CASES) * repeats} live turns")

    results = [await _run_model(pl_config, m, repeats) for m in models]
    _report(results)

    for r in results:
        assert r.samples, (
            f"{r.model} produced no samples — every turn failed to stream. "
            "The benchmark measured nothing; check the API key and model name."
        )
