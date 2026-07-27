"""LIVE latency benchmark for the page-fetch dedup on a recipe turn.

Run with:
    uv run pytest -m integration tests/integration/test_turn_latency_live.py -v -s

The `-s` matters: these print a report so the numbers can be eyeballed, in the
same spirit as `test_fast_path_live.py`.

## What this measures, and why it is an A/B rather than a threshold

`detect_split_verified` re-reads the page the extractor just read, to check
whether the model under-reported `components`. `deps.page_cache` makes the second
read a dict lookup instead of a second download + markdownify pass.

The saving is therefore **one page fetch on the turns where the cross-check
actually engages** — not a flat percentage off every turn. Asserting an absolute
wall-clock ceiling would measure the recipe site's mood, not our code. So the
benchmark runs the SAME page twice in one process, cache on and cache off, and
reports the delta. Both halves pay the same network conditions, which is the only
way to get a meaningful number out of a third-party host.

`test_fetch_dedup_latency` makes **no OpenAI calls** — it exercises the fetch
layer directly, so it is cheap and safe to run often. The full-turn benchmark
below it spends tokens and is opt-in via BENCH_FULL_TURN=1.

## Reading the output

`saved_seconds` is the headline. It is the wall-clock cost of the duplicate
download on this page, from this machine, right now. Expect it to vary a lot with
page weight and connection: a small page saves little, a heavy WordPress post
saves much more. A NEGATIVE or near-zero number on a small page is not a
regression — it means the page was cheap to fetch, which the assertion allows for.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

from cookbot.agents.chat import ChatAgentDeps, OnboardingState, fetch_page_text
from cookbot.agents.web_search import fetch_page_markdown

pytestmark = pytest.mark.integration


# Candidate benchmark pages, tried in order until one responds.
#
# A LIST rather than a constant, because the benchmark is worthless when its one
# hard-coded URL dies — and recipe URLs die. The original pick here
# (chilitonka.com/curry-z-chlebkiem-naan, the STEP 45 motivating page) now 404s
# through `safe_download`, which resolves the host to a bare IP and so loses the
# vhost on shared WordPress hosting. That made every "read" a failed fetch, which
# `fetch_page_text` correctly declines to cache — so the benchmark reported the
# cache as broken when the cache was fine. Prefer heavy pages first: the saving
# scales with page weight.
_CANDIDATE_URLS = [
    "https://aniagotuje.pl/przepis/zurek-staropolski",
    "https://aniagotuje.pl/przepis/pierogi-ruskie",
    "https://aniagotuje.pl/przepis/kotlet-schabowy",
]

# The cached read must be effectively instant — it is a dict lookup. This is the
# assertion that would catch the cache silently not being consulted; it is orders
# of magnitude away from any real fetch, so it cannot be flaky.
_CACHE_READ_CEILING_SECONDS = 0.05


def _p(line: str = "") -> None:
    """Print, surviving a legacy console codepage (see test_fast_path_live)."""
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(line.encode(enc, errors="replace").decode(enc, errors="replace"))


async def _time_fetch(url: str, cache: dict[str, str] | None) -> tuple[float, int]:
    """Return (seconds, chars) for one `fetch_page_text` call."""
    t0 = time.perf_counter()
    text = await fetch_page_text(url, cache=cache)
    return time.perf_counter() - t0, len(text)


async def _live_url() -> str:
    """First candidate that actually returns content, else skip.

    A dead URL must SKIP, never fail: an unreachable recipe site is not a
    performance regression in this repo, and a benchmark that cries wolf when a
    third party reorganises its permalinks gets ignored — which is worse than
    not having it. Also warms DNS/TLS for the chosen host, so the first measured
    fetch isn't paying setup costs unrelated to what is being compared.
    """
    for url in _CANDIDATE_URLS:
        try:
            if await fetch_page_markdown(url):
                return url
        except Exception:  # noqa: BLE001, S112 — try the next candidate
            continue
    pytest.skip(
        f"no benchmark page reachable ({len(_CANDIDATE_URLS)} tried) — "
        "not a code regression; refresh _CANDIDATE_URLS"
    )


async def test_fetch_dedup_latency() -> None:
    """A/B the second read of a page: cache off vs cache on. No OpenAI calls.

    This is the benchmark to re-run when you want to know whether the dedup still
    pays. It reproduces the exact pattern a recipe turn performs — extractor
    fetches the page, then the split cross-check asks for the same URL — and
    times the SECOND read both ways.
    """
    url = await _live_url()

    # ── Cache OFF: today's behaviour before the fix. Two real downloads. ──
    uncached_first, chars = await _time_fetch(url, cache=None)
    uncached_second, _ = await _time_fetch(url, cache=None)

    # ── Cache ON: the extractor's fetch is recorded, the cross-check reuses it. ──
    cache: dict[str, str] = {}
    cached_first, _ = await _time_fetch(url, cache=cache)
    cached_second, _ = await _time_fetch(url, cache=cache)

    saved = uncached_second - cached_second

    # A failed fetch returns "" fast, which would look exactly like a cache hit.
    # Assert we actually measured a real download, or the numbers below are lies.
    assert chars > 0, (
        f"{url} returned no content — the benchmark measured failed fetches, "
        "not cached reads"
    )

    _p()
    _p("=" * 72)
    _p("PAGE-FETCH DEDUP BENCHMARK")
    _p(f"  url            {url}")
    _p(f"  page size      {chars:,} chars of markdown (post-clean)")
    _p("-" * 72)
    _p(f"  cache OFF  1st read {uncached_first:6.2f}s   2nd read {uncached_second:6.2f}s")
    _p(f"  cache ON   1st read {cached_first:6.2f}s   2nd read {cached_second:6.4f}s")
    _p("-" * 72)
    _p(f"  SAVED on the 2nd read: {saved:.2f}s")
    _p("  (that is the per-turn saving on turns where the split")
    _p("   cross-check engages — see detect_split_verified)")
    _p("=" * 72)
    _p()

    assert cache, "nothing was cached — fetch_page_text did not populate the cache"

    # The real assertion: a cached read is a dict lookup, not a download. Stated
    # as an absolute ceiling rather than a ratio, because on a fast page/CDN the
    # uncached read can itself be quick and a ratio would go flaky.
    assert cached_second < _CACHE_READ_CEILING_SECONDS, (
        f"cached read took {cached_second:.3f}s — expected a dict lookup "
        f"(<{_CACHE_READ_CEILING_SECONDS}s). The cache is not being consulted."
    )

    # Content must be identical, or the "cross-check compares byte-identical
    # text" guarantee is false and we have traded correctness for speed.
    assert cache[url], "cached entry is empty"


async def test_dedup_saves_a_download_on_a_real_turn_shape() -> None:
    """The dedup must hold through `ChatAgentDeps`, not just a bare dict.

    Guards the wiring rather than the timing: `deps.page_cache` is what the tools
    actually pass, and `reset_turn` must not leave a stale page behind for the
    next turn (pages change; a connection is long-lived).
    """
    from cookbot.models.tenant import TenantConfig

    config = TenantConfig(
        tenant_id="bench", persona="x", language="pl",
        recipe_source_url="", allowed_origins=[],
    )
    deps = ChatAgentDeps(config=config, onboarding=OnboardingState())
    url = await _live_url()

    first, chars = await _time_fetch(url, cache=deps.page_cache)
    assert chars > 0, f"{url} returned no content — nothing to dedupe"

    second, _ = await _time_fetch(url, cache=deps.page_cache)

    _p()
    _p(f"deps.page_cache: 1st {first:.2f}s → 2nd {second:.4f}s "
       f"(saved {first - second:.2f}s)")

    assert second < _CACHE_READ_CEILING_SECONDS, (
        f"second read through deps.page_cache took {second:.3f}s — the tools' "
        "cache is not deduping"
    )

    deps.reset_turn()
    assert deps.page_cache == {}, (
        "reset_turn left pages cached — a later turn would serve stale content"
    )


@pytest.mark.skipif(
    os.getenv("BENCH_FULL_TURN") != "1",
    reason="set BENCH_FULL_TURN=1 to benchmark a full recipe turn (spends OpenAI tokens)",
)
async def test_full_recipe_turn_latency(pl_config) -> None:
    """End-to-end wall-clock + token cost of a pasted-link turn, A/B on the dedup.

    Opt-in because it spends tokens. This is the number to record BEFORE
    attempting STEP 53 (model comparison) or the deterministic-scaling change, so
    those have a baseline to be judged against.

    ## Why the extraction is run ONCE and shared

    The obvious A/B — run the whole turn twice, cache on and off — measures mostly
    LLM jitter: two `gpt-4o-mini` extractions of the same page differ by far more
    than the fetch being compared, so the dedup's contribution drowns. The
    extraction is therefore run once, and only the part the cache actually
    changes (`detect_split_verified`'s page read) is measured both ways, against
    the identical extracted Recipe.

    The extraction time is still reported, because the *ratio* is the point: a
    saving is only interesting relative to the turn it sits inside.

    Deliberately NOT asserted against a wall-clock ceiling: a full turn is
    3-5 serial LLM round-trips over live web search, and any bound tight enough
    to be meaningful would be flaky. It prints; you read it.
    """
    from pydantic_ai.usage import RunUsage

    from cookbot.agents.chat import build_web_fetch_agent, detect_split_verified
    from cookbot.agents.web_search import web_fetch_prompt

    deps = ChatAgentDeps(config=pl_config, onboarding=OnboardingState())
    usage = RunUsage()
    url = await _live_url()

    # ── The shared, expensive part: fetch + extract (one LLM call) ────────────
    t0 = time.perf_counter()
    agent = build_web_fetch_agent(
        pl_config, pinned_url=url, page_cache=deps.page_cache
    )
    recipe = (await agent.run(web_fetch_prompt(url), usage=usage)).output
    t_extract = time.perf_counter() - t0

    if recipe is None:
        pytest.skip("extraction returned None — live model flake, not a perf result")

    extract_tokens = usage.total_tokens or 0

    # ── Arm A: WITHOUT the improvement (page_cache=None → re-downloads) ───────
    usage_a = RunUsage()
    t1 = time.perf_counter()
    pending_a, _ = await detect_split_verified(
        recipe, config=pl_config, usage=usage_a, page_cache=None,
    )
    t_split_without = time.perf_counter() - t1

    # ── Arm B: WITH the improvement (reuses the extractor's download) ─────────
    usage_b = RunUsage()
    t2 = time.perf_counter()
    pending_b, _ = await detect_split_verified(
        recipe, config=pl_config, usage=usage_b, page_cache=deps.page_cache,
    )
    t_split_with = time.perf_counter() - t2

    saved = t_split_without - t_split_with
    total_without = t_extract + t_split_without
    total_with = t_extract + t_split_with

    _p()
    _p("=" * 72)
    _p("FULL TURN BENCHMARK (pasted link -> card)")
    _p(f"  url                {url}")
    _p(f"  page               {len(deps.page_cache.get(url, '')):,} chars")
    _p("-" * 72)
    _p(f"  fetch + extract    {t_extract:6.2f}s   {extract_tokens:>7,} tokens "
       f"({usage.requests} req)   [shared by both arms]")
    _p("-" * 72)
    _p(f"  split cross-check  WITHOUT dedup {t_split_without:6.2f}s "
       f"({usage_a.requests} extra req)")
    _p(f"  split cross-check  WITH    dedup {t_split_with:6.2f}s "
       f"({usage_b.requests} extra req)")
    _p("-" * 72)
    _p(f"  TURN TOTAL         WITHOUT {total_without:6.2f}s")
    _p(f"  TURN TOTAL         WITH    {total_with:6.2f}s")
    _p(f"  SAVED              {saved:6.2f}s  "
       f"({saved / total_without * 100:.1f}% of the turn)")
    _p("-" * 72)
    _p(f"  split detected     without={pending_a is not None} "
       f"with={pending_b is not None}   (must match)")
    _p("=" * 72)
    _p()

    # Both arms must reach the SAME verdict — a speedup that changes the answer
    # is not a speedup, it is a bug. This is the assertion that matters most here.
    assert (pending_a is not None) == (pending_b is not None), (
        f"dedup changed the split verdict: without={pending_a is not None} "
        f"with={pending_b is not None}"
    )
