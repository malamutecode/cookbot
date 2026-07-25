"""Shared guards for the agent unit tests.

The unit tier is hermetic: no network, no LLM (root CLAUDE.md, "Running Tests").
The STEP 47 fast path made that easy to violate by accident — `propose_recipes`
now has a branch that calls DuckDuckGo directly, so a test that stubs only
`build_recipe_options_agent` would silently make real search requests (this
happened: `test_propose_recipes_failure_returns_structured_error` fetched 20 live
results before the guard existed).

The autouse fixture below fails the fast path closed by default. A test that
wants to exercise it patches `cookbot.agents.chat.build_fast_proposals` itself,
which takes precedence.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_live_ddg_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the zero-LLM fast path return nothing unless a test stubs it."""
    async def _blocked(_query: str, limit: int):  # noqa: ANN202
        return []

    monkeypatch.setattr("cookbot.agents.chat.build_fast_proposals", _blocked)
