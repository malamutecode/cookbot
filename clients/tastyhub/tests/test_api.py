from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import VERSION, app
from app.middleware.auth import get_tenant_config
from cookbot.models.session import Session
from cookbot.models.tenant import TenantConfig

TEST_API_KEY = "tk_test_key"
TEST_TENANT_ID = "tastyhub"

_test_config = TenantConfig(
    tenant_id=TEST_TENANT_ID,
    persona="Test chef",
    language="en",
    recipe_source_url="",
    allowed_origins=[],
)


def _make_mock_firestore() -> AsyncMock:
    mock = AsyncMock()
    mock.save_session = AsyncMock(return_value=None)
    mock.get_session = AsyncMock(return_value=None)
    return mock


def _make_mock_settings() -> MagicMock:
    s = MagicMock(spec=Settings)
    s.tenant_id = TEST_TENANT_ID
    s.api_key = TEST_API_KEY
    s.session_ttl_hours = 24
    s.google_cloud_project = "test-project"
    s.firestore_database = "(default)"
    s.openai_api_key = "sk-test"
    s.model_chat = "gpt-4o-mini"
    s.model_recipe_gen = "gpt-4o-mini"
    s.model_web_search = "gpt-4o-mini"
    s.model_recipe_options = "gpt-4o-mini"
    s.model_shopping_list = "gpt-4o-mini"
    s.max_hitl_rounds = 3
    s.firestore_emulator_host = ""
    return s


@pytest.fixture()
def client() -> TestClient:
    # Pre-set state BEFORE TestClient starts lifespan
    app.state.settings = _make_mock_settings()
    app.state.firestore = _make_mock_firestore()
    app.dependency_overrides[get_tenant_config] = lambda: _test_config
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
    del app.state.settings
    del app.state.firestore


@pytest.fixture()
def client_no_override() -> TestClient:
    """Client with NO dependency override — auth runs for real."""
    app.state.settings = _make_mock_settings()
    app.state.firestore = _make_mock_firestore()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    del app.state.settings
    del app.state.firestore


# ── /health ───────────────────────────────────────────────────────────────────

def test_health_returns_200(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["tenant"] == TEST_TENANT_ID
    assert body["version"] == VERSION


# ── POST /v1/sessions ─────────────────────────────────────────────────────────

def test_create_session_valid_key(client: TestClient) -> None:
    resp = client.post("/v1/sessions", headers={"x-api-key": TEST_API_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert "expires_at" in body
    assert len(body["session_id"]) == 36  # UUID4


def test_create_session_saves_to_firestore(client: TestClient) -> None:
    resp = client.post("/v1/sessions", headers={"x-api-key": TEST_API_KEY})
    assert resp.status_code == 200
    app.state.firestore.save_session.assert_called_once()
    saved: Session = app.state.firestore.save_session.call_args[0][0]
    assert saved.tenant_id == TEST_TENANT_ID


def test_create_session_invalid_key_returns_401(client_no_override: TestClient) -> None:
    resp = client_no_override.post("/v1/sessions", headers={"x-api-key": "wrong_key"})
    assert resp.status_code == 401
    assert "Invalid API key" in resp.json()["detail"]


def test_create_session_missing_auth_returns_401(client_no_override: TestClient) -> None:
    # No x-api-key and no Authorization header → 401 (not 422, since both headers are optional)
    resp = client_no_override.post("/v1/sessions")
    assert resp.status_code == 401


def test_create_session_valid_key_no_override(client_no_override: TestClient) -> None:
    resp = client_no_override.post(
        "/v1/sessions", headers={"x-api-key": TEST_API_KEY}
    )
    assert resp.status_code == 200
    assert "session_id" in resp.json()
