"""Performance regression guards for a chat turn.

These are UNIT tests (hermetic, no network, no LLM) that assert the *shape* of
the work a turn does rather than its wall-clock time: how many times a page is
downloaded, and whether the user is told what is happening while a slow tool
runs. Wall-clock belongs in the live tier and is far too noisy to gate on —
counting round-trips catches the same regressions deterministically.

Two properties are locked in here:

1. **A page is fetched ONCE per turn.** `detect_split_verified`'s cross-check
   re-reads the page the extractor just read. Without the per-turn page cache
   that is a second full download + markdownify pass of the same URL (one live
   page measured at ~238k chars pre-clean), on the single slowest common turn.
2. **Slow tools emit progress.** `run_stream` yields nothing until the whole
   tool chain finishes, so without progress events the user watches a spinner
   for the entire fetch → extract → scale chain.
"""
import pytest

from cookbot.agents import chat as chat_mod
from cookbot.agents.chat import ChatAgentDeps, ProgressEvent, resolve_recipe
from cookbot.models.recipe import Recipe, RecipeSummary
from cookbot.models.tenant import TenantConfig

_URL = "https://example.com/przepis/curry-z-naan"

# Captured at import, before the suite's autouse fixture replaces it. The tests
# below exercise `fetch_page_text` ITSELF (its caching), so they need the real
# implementation; they stay hermetic by stubbing `fetch_page_markdown` under it.
_REAL_FETCH_PAGE_TEXT = chat_mod.fetch_page_text


def _recipe() -> Recipe:
    return Recipe(
        name="Curry",
        description="",
        ingredients=["200 g ryżu", "1 cebula"],
        steps=["Gotuj."],
        prep_time_minutes=10,
        cook_time_minutes=20,
        difficulty="Easy",
        servings=4,
        tips=[],
        source_url=_URL,
    )


class _FakeRun:
    def __init__(self, output: object) -> None:
        self.output = output


class _FakeAgent:
    """Stands in for a sub-agent: records calls, returns a canned output."""

    def __init__(self, output: object) -> None:
        self._output = output
        self.calls = 0

    async def run(self, *_a: object, **_kw: object) -> _FakeRun:
        self.calls += 1
        return _FakeRun(self._output)


@pytest.fixture
def config() -> TenantConfig:
    # Same shape as the rest of the agent suite (tests/test_agents/test_chat.py).
    return TenantConfig(
        tenant_id="test",
        persona="You are a helpful chef",
        language="en",
        recipe_source_url="",
        allowed_origins=[],
    )


# ── 1. The page is downloaded once per turn ───────────────────────────────────


async def test_split_crosscheck_reuses_the_extractors_fetch(
    monkeypatch: pytest.MonkeyPatch, config: TenantConfig
) -> None:
    """The split cross-check must not re-download a page already fetched.

    `detect_split_verified` needs the page markdown to check whether the model
    under-reported `components`. The extractor's own `web_fetch` already pulled
    that exact URL microseconds earlier, so a second download is pure latency on
    the slowest turn there is.
    """
    downloads: list[str] = []

    async def _count_download(url: str, *_a: object, **_kw: object) -> str:
        downloads.append(url)
        # Two serving headings → the cross-check path actually engages.
        return "Skladniki dla 4 osob ... Skladniki na 8 porcji ..."

    # The suite's autouse guard neuters `fetch_page_text` to keep the tier
    # hermetic; this test is specifically ABOUT that function, so restore the
    # real one and stub the layer below it instead (still no network).
    monkeypatch.setattr(chat_mod, "fetch_page_text", _REAL_FETCH_PAGE_TEXT)
    monkeypatch.setattr(chat_mod, "fetch_page_markdown", _count_download)

    deps = ChatAgentDeps(config=config)
    # Simulate the extractor's fetch: this is what the fetch tool records.
    await chat_mod.fetch_page_text(_URL, cache=deps.page_cache)
    assert len(downloads) == 1, "sanity: the first fetch is a real download"

    # The cross-check asks for the same page again, in the same turn.
    await chat_mod.fetch_page_text(_URL, cache=deps.page_cache)

    assert len(downloads) == 1, (
        f"page downloaded {len(downloads)}x in one turn — the extractor's fetch "
        "should be reused by the split cross-check"
    )


async def test_fetch_tool_populates_the_cache_the_crosscheck_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two halves of the dedup must actually meet.

    The test above proves `fetch_page_text` reuses a cached entry; this proves the
    entry gets there in the first place. Without this, the cache silently never
    hits and the second download comes back with nothing to catch it — the real
    failure mode, since both halves look correct in isolation.
    """
    from cookbot.agents.web_search import recipe_web_fetch_tool

    async def _fake_download(url: str, *_a: object, **_kw: object) -> object:
        return {"url": url, "title": "t", "content": "page markdown"}

    monkeypatch.setattr("cookbot.agents.web_search._fetch_markdown", _fake_download)

    cache: dict[str, str] = {}
    tool = recipe_web_fetch_tool(pinned_url=_URL, page_cache=cache)
    await tool.function(_URL)  # type: ignore[operator]

    assert cache.get(_URL) == "page markdown", (
        f"the fetch tool did not record {_URL} — the cross-check's lookup "
        f"will miss and re-download. Cache held: {list(cache)}"
    )


async def test_page_cache_is_scoped_to_one_turn(config: TenantConfig) -> None:
    """A cached page must not leak across turns.

    Pages change, and a chat connection is long-lived. Caching for the duration
    of a connection would serve a user stale content indefinitely; the whole
    value here is deduping *within* a single turn's tool chain.
    """
    deps = ChatAgentDeps(config=config)
    deps.page_cache[_URL] = "stale text"

    deps.reset_turn()

    assert deps.page_cache == {}, "page cache must be cleared by reset_turn()"


async def test_page_cache_survives_a_failed_fetch(
    monkeypatch: pytest.MonkeyPatch, config: TenantConfig
) -> None:
    """A failed fetch must degrade to "no cross-check", never crash the turn.

    `fetch_page_text` is best-effort by contract (Rule 7). Adding a cache must
    not turn a network hiccup into an exception.
    """
    async def _boom(_url: str, *_a: object, **_kw: object) -> str:
        raise RuntimeError("network down")

    monkeypatch.setattr(chat_mod, "fetch_page_text", _REAL_FETCH_PAGE_TEXT)
    monkeypatch.setattr(chat_mod, "fetch_page_markdown", _boom)

    deps = ChatAgentDeps(config=config)
    assert await chat_mod.fetch_page_text(_URL, cache=deps.page_cache) == ""


# ── 2. Slow tools tell the user what they are doing ───────────────────────────


async def test_resolve_recipe_emits_progress_before_the_slow_work(
    monkeypatch: pytest.MonkeyPatch, config: TenantConfig
) -> None:
    """Resolving a proposal is the slowest turn — it must announce itself.

    `agent.run_stream` emits no token until every tool has returned, so a turn
    that fetches, extracts and scales shows a bare spinner for its whole
    duration. The progress event is what the WS handler turns into "Czytam
    stronę…".
    """
    fetch_agent = _FakeAgent(_recipe())
    monkeypatch.setattr(
        chat_mod, "build_web_fetch_agent", lambda *_a, **_kw: fetch_agent
    )
    monkeypatch.setattr(
        chat_mod, "build_recipe_scale_agent", lambda *_a: _FakeAgent(None)
    )

    events: list[ProgressEvent] = []
    selected = RecipeSummary(
        name="Curry",
        description="",
        source="web_search",
        source_url=_URL,
        difficulty="Easy",
        total_time_minutes=30,
        key_ingredients=[],
    )

    await resolve_recipe(
        selected,
        "1",
        chat_mod.OnboardingState(servings=4),
        config=config,
        site_filter="",
        allow_ai_generated=False,
        on_progress=events.append,
    )

    assert events, "resolve_recipe emitted no progress event for a web fetch"
    assert events[0].kind == "progress"
    assert events[0].message, "a progress event needs user-facing text"


async def test_progress_event_is_a_turn_event(config: TenantConfig) -> None:
    """Progress must ride the existing ordered-events rail, not a direct WS send.

    Rule 4: tools append to `deps.events` and the handler emits them. A tool that
    reached for the websocket directly would break ordering and testability.
    """
    deps = ChatAgentDeps(config=config)
    deps.events.append(ProgressEvent(message="Czytam stronę…"))

    assert deps.events[0].kind == "progress"

    deps.reset_turn()
    assert deps.events == [], "progress events must be cleared between turns"
