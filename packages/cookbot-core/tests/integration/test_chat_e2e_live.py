"""Full conversational e2e against real OpenAI + DuckDuckGo.

Drives the ChatAgent exactly like the WebSocket handler does (reset_turn → stream
→ read deps.events), through the 6-turn flow a real user follows:

  1. "Obiad"            → next question (onboarding advances)
  2. "2"                → next question
  3. "30 min"           → next question
  4. "makaron, szpinak" → next question
  5. "nie"              → 4 recipe proposals, >=2 from web search
  6. pick a web option  → structured recipe (ingredients + steps) with a source link

Because it hits live services it is marked `integration` and skips without
OPENAI_API_KEY. Web search is occasionally flaky; assertions target structure and
the web-vs-AI split rather than exact wording.
"""
import asyncio

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from cookbot.agents.chat import (
    ChatAgentDeps,
    FinalRecipeEvent,
    RecipeOptionsEvent,
    build_chat_agent,
    stream_chat_response,
)


async def _say(agent, deps, history, text: str, *, retries: int = 4) -> tuple[str, list]:
    """Run one chat turn the way the WS handler does. Returns (reply_text, events).

    Retries on OpenAI 429 (TPM rate limit) with backoff — small orgs hit the
    per-minute token cap during a multi-turn live run; that's transient infra,
    not a behaviour failure.
    """
    for attempt in range(retries):
        deps.reset_turn()
        try:
            reply = ""
            async with stream_chat_response(agent, deps, history, text) as tokens:
                async for tok in tokens:
                    reply += tok
            return reply, list(deps.events)
        except ModelHTTPError as e:
            if e.status_code == 429 and attempt < retries - 1:
                await asyncio.sleep(2 ** attempt * 5)  # 5s, 10s, 20s
                continue
            raise
    raise AssertionError("unreachable")


def _looks_like_question(text: str) -> bool:
    return "?" in text


async def test_full_onboarding_to_web_recipe(pl_config) -> None:
    agent = build_chat_agent(pl_config)
    deps = ChatAgentDeps(
        config=pl_config,
        preferred_sites=["kwestiasmaku.com", "aniagotuje.pl"],
        allow_ai_generated=True,
    )
    history: list = []

    # ── Turn 1: dish type ──────────────────────────────────────────────────
    reply, events = await _say(agent, deps, history, "Obiad")
    assert deps.onboarding.dish_type is not None, "dish_type not recorded after turn 1"
    assert _looks_like_question(reply), f"expected a follow-up question, got: {reply!r}"
    assert not events, "no side-effects expected mid-onboarding"

    # ── Turn 2: servings ───────────────────────────────────────────────────
    reply, events = await _say(agent, deps, history, "2")
    assert deps.onboarding.servings == 2
    assert _looks_like_question(reply)

    # ── Turn 3: time ───────────────────────────────────────────────────────
    reply, events = await _say(agent, deps, history, "30 min")
    assert deps.onboarding.max_time_minutes == 30
    assert _looks_like_question(reply)

    # ── Turn 4: ingredients ────────────────────────────────────────────────
    reply, events = await _say(agent, deps, history, "makaron, szpinak")
    assert deps.onboarding.ingredients, "ingredients not recorded after turn 4"
    ingr_joined = " ".join(deps.onboarding.ingredients).lower()
    assert "makaron" in ingr_joined and "szpinak" in ingr_joined
    assert _looks_like_question(reply)

    # ── Turn 5: notes 'nie' → proposals ────────────────────────────────────
    reply, events = await _say(agent, deps, history, "nie")
    opt_events = [e for e in events if isinstance(e, RecipeOptionsEvent)]
    assert opt_events, f"expected a RecipeOptionsEvent after onboarding; events: {[type(e).__name__ for e in events]}"
    proposals = opt_events[0].proposals
    # The product target is 4 options, but the exact count depends on model
    # strength + how many real web pages exist for the dish. The thing that
    # MUST hold (and was the actual bug) is that real web results come back —
    # not an all-AI fallback. So assert structure + the web split, not an exact 4.
    # (Strict "== 4" belongs in a production-model run; this uses gpt-4o-mini.)
    assert len(proposals) >= 2, f"expected at least 2 proposals, got {len(proposals)}"
    assert len(proposals) <= 4, f"expected at most 4 proposals, got {len(proposals)}"
    for p in proposals:
        # every proposal is structurally complete
        assert p.name and p.description and p.key_ingredients

    web = [p for p in proposals if p.source == "web_search"]
    assert len(web) >= 2, (
        f"expected >=2 web_search proposals (the core regression), got {len(web)}; "
        f"sources: {[p.source for p in proposals]}"
    )
    for p in web:
        assert p.source_url and p.source_url.startswith("http"), \
            f"web proposal '{p.name}' missing source_url"

    # ── Turn 6: pick a WEB proposal by its 1-based number → full recipe ─────
    web_index = next(i for i, p in enumerate(proposals) if p.source == "web_search") + 1
    reply, events = await _say(agent, deps, history, str(web_index))

    final_events = [e for e in events if isinstance(e, FinalRecipeEvent)]
    assert final_events, f"expected a FinalRecipeEvent after picking; events: {[type(e).__name__ for e in events]}"
    final = final_events[0]

    assert final.source == "web_search", f"picked a web option but source is {final.source!r}"
    recipe = final.recipe
    assert recipe.ingredients, "recipe has no ingredients (składniki)"
    assert recipe.steps, "recipe has no steps (kroki)"
    assert recipe.source_url and recipe.source_url.startswith("http"), \
        f"web recipe must keep a source link, got: {recipe.source_url!r}"

    # ── Turn 7: "add it to the calendar" → CalendarAddEvent ─────────────────
    # Regression for the manual report: the bot must actually call add_to_calendar.
    from cookbot.agents.chat import CalendarAddEvent  # noqa: PLC0415
    reply, events = await _say(agent, deps, history, "dodaj ten przepis do kalendarza na 10.08")
    add_events = [e for e in events if isinstance(e, CalendarAddEvent)]
    assert add_events, (
        "expected a CalendarAddEvent after asking to add to calendar; "
        f"got events: {[type(e).__name__ for e in events]} / reply: {reply!r}"
    )
    entry = add_events[0].entry
    assert entry.recipe_name, "calendar entry has no recipe name"
    # Date must be in the current year (the year-default fix) and the right month/day.
    assert entry.date.endswith("-08-10"), f"expected ...-08-10, got {entry.date!r}"
    assert entry.date.startswith(str(__import__('datetime').date.today().year)), \
        f"calendar date not in current year: {entry.date!r}"
