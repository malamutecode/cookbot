"""Grocery route tests — hermetic (search + feed stubbed, LLM re-rank overridden).

Since STEP 50 the route prefers a shop's own search backend and only falls back to
the downloaded feed when that is unreachable. Both paths are stubbed here: no
network, no LLM. The default fixture disables search so the historical feed-path
assertions keep testing the feed; ``search_client`` covers the search path.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from delivery_shops.models import Product, ProductMatch
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import app


def _make_mock_settings() -> MagicMock:
    s = MagicMock(spec=Settings)
    s.tenant_id = "tastyhub"
    s.api_key = "tk_test_key"
    s.session_ttl_hours = 24
    s.google_cloud_project = "test-project"
    s.firestore_database = "(default)"
    s.openai_api_key = "sk-test"
    s.firestore_emulator_host = ""
    return s


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    app.state.settings = _make_mock_settings()
    app.state.firestore = AsyncMock()

    # Stub the Frisco feed so no network call happens. Two "masło" products make
    # an ambiguous shortlist that exercises the re-rank path.
    fake_products = [
        Product(id="1", name="Cebula obrana", keywords="cebula", url="https://frisco/1", price=4.79),
        Product(id="2", name="Mąka pszenna typ 550", keywords="maka", url="https://frisco/2", price=2.99),
        Product(id="3", name="Masło z czosnkiem", category="Masło", keywords="maslo", url="https://frisco/3"),
        Product(id="4", name="Masło extra", category="Masło", keywords="maslo", url="https://frisco/4"),
    ]

    async def fake_load(self):  # noqa: ANN001
        return fake_products, "2026-07-04T00:00:00+02:00"

    from delivery_shops.shops.frisco import FriscoShop

    monkeypatch.setattr(FriscoShop, "load", fake_load)
    # Force the feed path: these cases predate STEP 50 and assert feed behaviour
    # (lexical matching, generated_at). Removing search_many makes supports_search
    # report False, exactly as it would for a feed-only shop.
    monkeypatch.delattr(FriscoShop, "search_many", raising=False)

    # Override the LLM re-ranker with a TestModel so no real API call happens.
    # TestModel fills the structured output with defaults (choice=None → the route
    # falls back to the lexical #1), keeping tests deterministic and offline.
    from cookbot.agents.product_rerank import ReRankChoice
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    import app.api.grocery as grocery_mod

    def _build_test_agent(config):  # noqa: ANN001
        return Agent(TestModel(), output_type=ReRankChoice)

    monkeypatch.setattr(grocery_mod, "build_product_rerank_agent", _build_test_agent)

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    del app.state.settings
    del app.state.firestore
    if hasattr(app.state, "grocery_matchers"):
        del app.state.grocery_matchers


def test_match_returns_products(client: TestClient) -> None:
    resp = client.post("/v1/grocery/frisco/match", json={"ingredients": ["cebula", "mąka pszenna"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["shop"] == "frisco"
    assert data["generated_at"] == "2026-07-04T00:00:00+02:00"
    matched_ids = {m["product"]["id"] for m in data["matched"]}
    assert matched_ids == {"1", "2"}


def test_unmatched_goes_to_bucket(client: TestClient) -> None:
    resp = client.post("/v1/grocery/frisco/match", json={"ingredients": ["kawior z bieługi"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"] == []
    assert [u["ingredient"] for u in data["unmatched"]] == ["kawior z bieługi"]


def test_empty_ingredients_returns_empty(client: TestClient) -> None:
    resp = client.post("/v1/grocery/frisco/match", json={"ingredients": []})
    assert resp.status_code == 200
    assert resp.json() == {"shop": "frisco", "matched": [], "unmatched": [], "generated_at": None}


def test_unknown_shop_is_404(client: TestClient) -> None:
    resp = client.post("/v1/grocery/rohlik/match", json={"ingredients": ["cebula"]})
    assert resp.status_code == 404


def test_ambiguous_match_falls_back_to_lexical_when_reranker_declines(client: TestClient) -> None:
    # "masło" has two candidates → the re-rank path runs. TestModel returns
    # choice=None, so the route keeps the lexical #1. Either masło product is a
    # valid outcome; the point is a single matched product with no crash.
    resp = client.post("/v1/grocery/frisco/match", json={"ingredients": ["masło"]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["matched"]) == 1
    assert data["matched"][0]["product"]["id"] in {"3", "4"}


# ---------------------------------------------------------------------------
# Search path (STEP 50)
# ---------------------------------------------------------------------------


def _search_match(ingredient: str, product_id: str, name: str) -> ProductMatch:
    return ProductMatch(
        ingredient=ingredient,
        shop="frisco",
        product=Product(
            id=product_id,
            name=name,
            category="Nabiał Masło Tradycyjne",
            url=f"https://www.frisco.pl/pid,{product_id}/stn,product",
            price=6.19,
        ),
        score=14015.0,
    )


@pytest.fixture()
def search_client(client: TestClient, monkeypatch) -> TestClient:
    """The default client, with the shop's search backend stubbed back in."""
    from delivery_shops.shops.frisco import FriscoShop

    async def fake_search_many(self, queries, limit=5):  # noqa: ANN001
        return {
            q: [
                _search_match(q, "api-1", "Masło extra"),
                _search_match(q, "api-2", "Masło z czosnkiem"),
            ]
            for q in queries
        }

    monkeypatch.setattr(FriscoShop, "search_many", fake_search_many, raising=False)
    return client


def test_search_path_is_preferred_over_the_feed(search_client: TestClient) -> None:
    resp = search_client.post("/v1/grocery/frisco/match", json={"ingredients": ["masło"]})
    assert resp.status_code == 200
    data = resp.json()
    # Feed ids are "1".."4"; an "api-" id proves the search backend answered.
    assert data["matched"][0]["product"]["id"] == "api-1"
    # Live results carry no catalogue date.
    assert data["generated_at"] is None


def test_search_path_skips_the_llm_rerank_by_default(search_client: TestClient, monkeypatch) -> None:
    """grocery_llm_rerank defaults to False — the agent must never be built."""
    import app.api.grocery as grocery_mod

    def _boom(config):  # noqa: ANN001
        raise AssertionError("re-ranker must not run when grocery_llm_rerank is False")

    monkeypatch.setattr(grocery_mod, "build_product_rerank_agent", _boom)

    resp = search_client.post("/v1/grocery/frisco/match", json={"ingredients": ["masło"]})
    assert resp.status_code == 200
    # Frisco's own ranking wins: the first candidate is kept.
    assert resp.json()["matched"][0]["product"]["id"] == "api-1"


def test_search_path_reranks_when_the_tenant_opts_in(search_client: TestClient, monkeypatch) -> None:
    from app.config.tenant import TASTYHUB_CONFIG

    monkeypatch.setattr(TASTYHUB_CONFIG, "grocery_llm_rerank", True)

    called: list[str] = []

    async def fake_rerank(ingredient, candidates, agent):  # noqa: ANN001
        called.append(ingredient)
        return candidates[1]  # deliberately not the API's first pick

    import app.api.grocery as grocery_mod

    monkeypatch.setattr(grocery_mod, "_rerank", fake_rerank)

    resp = search_client.post("/v1/grocery/frisco/match", json={"ingredients": ["masło"]})
    assert resp.status_code == 200
    assert called == ["masło"]
    assert resp.json()["matched"][0]["product"]["id"] == "api-2"


def test_search_failure_falls_back_to_the_feed(client: TestClient, monkeypatch) -> None:
    """A dead search backend must degrade to feed matching, not to a 500."""
    from delivery_shops.shops.frisco import FriscoShop

    async def exploding_search_many(self, queries, limit=5):  # noqa: ANN001
        raise RuntimeError("frisco search is down")

    monkeypatch.setattr(FriscoShop, "search_many", exploding_search_many, raising=False)

    resp = client.post("/v1/grocery/frisco/match", json={"ingredients": ["cebula"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched"][0]["product"]["id"] == "1"  # from the stubbed feed
    assert data["generated_at"] == "2026-07-04T00:00:00+02:00"


def test_search_path_reports_unmatched_ingredients(search_client: TestClient, monkeypatch) -> None:
    from delivery_shops.shops.frisco import FriscoShop

    async def partial_search_many(self, queries, limit=5):  # noqa: ANN001
        return {q: ([] if q == "kawior z bieługi" else [_search_match(q, "api-1", "Masło")]) for q in queries}

    monkeypatch.setattr(FriscoShop, "search_many", partial_search_many, raising=False)

    resp = search_client.post("/v1/grocery/frisco/match", json={"ingredients": ["masło", "kawior z bieługi"]})
    assert resp.status_code == 200
    data = resp.json()
    assert [u["ingredient"] for u in data["unmatched"]] == ["kawior z bieługi"]
    assert len(data["matched"]) == 1
