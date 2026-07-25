"""LIVE latency + quality check for the zero-LLM fast path (STEP 47).

Run with:
    uv run pytest -m integration tests/integration/test_fast_path_live.py -v -s

The `-s` matters: these tests print a full report of what came back (timings, the
actual cards, image/metadata hit rates) so the result can be eyeballed, not just
trusted. The acceptance criterion of STEP 47 is LATENCY, so it is asserted here
rather than left to a vibe.

This hits DuckDuckGo and real recipe sites, but makes NO OpenAI calls — the whole
point of the fast path. `test_fast_path_vs_llm_path_latency` is the exception and
is opt-in via COMPARE_LLM_PATH=1, because it does spend tokens.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

from cookbot.agents.chat import OnboardingState
from cookbot.agents.recipe_search_fast import (
    build_fast_proposals,
    fast_path_query,
    is_fast_path_request,
)

pytestmark = pytest.mark.integration

# Wall-clock ceiling for a fast-path turn.
#
# Measured on this machine (2026-07-25), per dish: jagodzianki 3.44s, pierogi
# ruskie 4.85s, żurek 5.50s. The A/B run put the LLM path it replaces at 11.73s
# for 4 cards, vs 3.50s for 6 — a 3.35x speedup at 2629 tokens → 0.
#
# The floor is DuckDuckGo itself (~1.9-2.8s, measured, and outside our control);
# enrichment is capped separately at _ENRICH_TOTAL_BUDGET_SECONDS. 8s is
# deliberately loose: this test guards against the fast path silently NOT
# engaging (which would read as ~12s), not against second-to-second search
# jitter. A tight bound here would just be flaky against a third-party service.
_LATENCY_BUDGET_SECONDS = 8.0

_DISHES = ["jagodzianki", "żurek", "pierogi ruskie"]


def _p(line: str = "") -> None:
    """Print, surviving a legacy console codepage.

    Windows terminals default to cp1250 here, which cannot encode characters that
    appear in real page titles (⭐, emoji). An UnicodeEncodeError in the REPORT
    must never fail the test — the report is diagnostics, not an assertion.
    """
    try:
        print(line)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(line.encode(enc, errors="replace").decode(enc, errors="replace"))


def _report(dish: str, query: str, elapsed: float, proposals: list) -> None:
    """Print a human-readable card dump — this is the point of running with -s."""
    _p(f"\n{'=' * 78}")
    _p(f"  DISH:    {dish!r}")
    _p(f"  QUERY:   {query!r}")
    _p(f"  ELAPSED: {elapsed:.2f}s   (budget {_LATENCY_BUDGET_SECONDS}s)")
    _p(f"  CARDS:   {len(proposals)}")
    with_img = sum(1 for p in proposals if p.image_url)
    with_meta = sum(1 for p in proposals if p.total_time_minutes or p.key_ingredients)
    _p(f"  IMAGES:  {with_img}/{len(proposals)} have an og:image")
    _p(f"  META:    {with_meta}/{len(proposals)} have JSON-LD time/ingredients")
    _p(f"{'-' * 78}")
    for i, p in enumerate(proposals, 1):
        _p(f"  [{i}] {p.name}")
        _p(f"      url:   {p.source_url}")
        _p(f"      desc:  {(p.description or '(none)')[:100]}")
        img = p.image_url or "(none)"
        _p(f"      image: {img[:100]}")
        meta = []
        if p.total_time_minutes:
            meta.append(f"{p.total_time_minutes} min")
        if p.key_ingredients:
            meta.append(", ".join(p.key_ingredients[:4]))
        _p(f"      meta:  {' | '.join(meta) if meta else '(none - chips hidden)'}")
    _p(f"{'=' * 78}\n")


@pytest.mark.parametrize("dish", _DISHES)
async def test_fast_path_returns_real_cards_within_budget(dish: str) -> None:
    """The headline test: a plain dish request yields real, linkable cards fast."""
    ob = OnboardingState(dish_type=dish)
    message = f"znajdź przepis na {dish}"

    assert is_fast_path_request(ob, message) is True, "request did not qualify for the fast path"

    query = fast_path_query(ob)
    started = time.monotonic()
    proposals = await build_fast_proposals(query, limit=6)
    elapsed = time.monotonic() - started

    _report(dish, query, elapsed, proposals)

    assert len(proposals) >= 3, f"only {len(proposals)} usable results — ranking may be too strict"
    assert elapsed < _LATENCY_BUDGET_SECONDS, f"fast path took {elapsed:.2f}s"

    for p in proposals:
        # Every card must be a real, clickable page — provenance is sacred.
        assert p.source == "web_search"
        assert p.source_url and p.source_url.startswith("http"), f"bad source_url: {p.source_url!r}"
        assert p.name.strip(), "card has no name"
        # Zero-LLM cards never invent difficulty.
        assert p.difficulty == "", f"difficulty was fabricated: {p.difficulty!r}"

    # One card per domain — six results from one site is not a real choice.
    domains = [p.source_url.split("/")[2] for p in proposals if p.source_url]
    assert len(domains) == len(set(domains)), f"duplicate domains: {domains}"


async def test_image_coverage_is_usable() -> None:
    """The card layout is image-led, so a run where almost nothing resolves an
    og:image would be a visual regression even if the timing looks great."""
    ob = OnboardingState(dish_type="jagodzianki")
    proposals = await build_fast_proposals(fast_path_query(ob), limit=6)
    _report("jagodzianki (image coverage)", fast_path_query(ob), 0.0, proposals)

    with_image = sum(1 for p in proposals if p.image_url)
    assert with_image >= len(proposals) // 2, (
        f"only {with_image}/{len(proposals)} cards resolved an og:image"
    )


@pytest.mark.skipif(
    os.getenv("COMPARE_LLM_PATH") != "1",
    reason="set COMPARE_LLM_PATH=1 to run the A/B latency comparison (spends OpenAI tokens)",
)
async def test_fast_path_vs_llm_path_latency(pl_config) -> None:
    """A/B the two paths on the same dish and print the speedup.

    Opt-in because the LLM leg costs money. This is the measurement that
    justifies the whole STEP, so it is worth running once by hand.
    """
    from cookbot.agents.recipe_options import (
        build_recipe_options_agent,
        populate_proposal_images,
        recipe_options_prompt,
    )
    from cookbot.models.recipe import ParsedIngredients, UserIntent

    dish = "jagodzianki"
    ob = OnboardingState(dish_type=dish)

    started = time.monotonic()
    fast = await build_fast_proposals(fast_path_query(ob), limit=6)
    fast_elapsed = time.monotonic() - started

    intent = UserIntent(dish_type=dish, servings=2, max_time_minutes=0,
                        available_ingredients=[], free_notes="")
    parsed = ParsedIngredients(items=[], must_use=[], dietary_hints=[], missing_staples=[])
    agent = build_recipe_options_agent(pl_config)

    started = time.monotonic()
    result = await agent.run(recipe_options_prompt(parsed, intent))
    slow = result.output.proposals[:4]
    await populate_proposal_images(slow)
    slow_elapsed = time.monotonic() - started

    speedup = slow_elapsed / fast_elapsed if fast_elapsed else 0
    _p(f"\n{'=' * 78}")
    _p("  A/B LATENCY COMPARISON - same dish, both paths")
    _p(f"{'-' * 78}")
    _p(f"  FAST (zero-LLM):    {fast_elapsed:6.2f}s  → {len(fast)} cards")
    _p(f"  SLOW (RecipeOpts):  {slow_elapsed:6.2f}s  → {len(slow)} cards")
    _p(f"  SPEEDUP:            {speedup:6.2f}x")
    _p(f"  TOKENS (slow path): {result.usage().total_tokens}")
    _p("  TOKENS (fast path): 0")
    _p(f"{'=' * 78}\n")

    assert fast_elapsed < slow_elapsed, "fast path was not faster"
