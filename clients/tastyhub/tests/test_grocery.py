"""Grocery route tests — hermetic (feed stubbed, LLM re-rank overridden)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from delivery_shops.models import Product
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
