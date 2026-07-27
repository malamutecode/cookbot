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


@pytest.fixture(autouse=True)
def _no_live_page_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neuter the STEP 45 split cross-check's page fetch.

    `detect_split_verified` re-fetches the page text (no LLM) to check whether the
    extractor missed ingredient headings. That is a real HTTP call, so it would
    silently un-hermetic the unit tier — the same trap the fast path sprang above.
    Returning "" means "no cross-check available", which is the designed
    fail-safe: behaviour falls back to trusting `components`.
    """
    async def _blocked(_url: str, cache: dict[str, str] | None = None) -> str:
        return ""

    monkeypatch.setattr("cookbot.agents.chat.fetch_page_text", _blocked)
