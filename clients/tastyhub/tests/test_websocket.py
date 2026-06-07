from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import app
from app.middleware.auth import get_tenant_config
from cookbot.models.session import Session, SessionStatus
from cookbot.models.spizarnia import Spizarnia, SpizarniaItem
from cookbot.models.tenant import TenantConfig
from cookbot.protocols.ws_messages import WsMessageType

TEST_TENANT_ID = "tastyhub"
TEST_API_KEY = "tk_test_key"

_test_config = TenantConfig(
    tenant_id=TEST_TENANT_ID,
    persona="Test chef",
    language="en",
    recipe_source_url="",
    allowed_origins=[],
)


def _make_session(session_id: str, *, expired: bool = False, uid: str | None = None) -> Session:
    now = datetime.now(UTC)
    expires_at = now - timedelta(hours=1) if expired else now + timedelta(hours=24)
    return Session(
        session_id=session_id,
        tenant_id=TEST_TENANT_ID,
        uid=uid,
        status=SessionStatus.ACTIVE,
        created_at=now,
        expires_at=expires_at,
    )


def _make_mock_firestore(session: Session | None = None) -> AsyncMock:
    mock = AsyncMock()
    mock.save_session = AsyncMock(return_value=None)
    mock.get_session = AsyncMock(return_value=session)
    mock.save_message = AsyncMock(return_value=None)
    mock.save_hitl_checkpoint = AsyncMock(return_value=None)
    mock.clear_hitl_checkpoint = AsyncMock(return_value=None)
    mock.get_hitl_checkpoint = AsyncMock(return_value=None)
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
    s.dev_uid = ""
    return s


@pytest.fixture()
def valid_session_id() -> str:
    return "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture()
def client_with_session(valid_session_id: str) -> TestClient:
    app.state.settings = _make_mock_settings()
    app.state.firestore = _make_mock_firestore(session=_make_session(valid_session_id))
    app.dependency_overrides[get_tenant_config] = lambda: _test_config
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
    del app.state.settings
    del app.state.firestore


@pytest.fixture()
def client_expired_session(valid_session_id: str) -> TestClient:
    app.state.settings = _make_mock_settings()
    app.state.firestore = _make_mock_firestore(session=_make_session(valid_session_id, expired=True))
    app.dependency_overrides[get_tenant_config] = lambda: _test_config
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
    del app.state.settings
    del app.state.firestore


@pytest.fixture()
def client_no_session() -> TestClient:
    app.state.settings = _make_mock_settings()
    app.state.firestore = _make_mock_firestore(session=None)
    app.dependency_overrides[get_tenant_config] = lambda: _test_config
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
    del app.state.settings
    del app.state.firestore


# ── Fake streaming chat agent ────────────────────────────────────────────────

def _patch_chat_agent(token: str = "Hello from mock agent!"):
    """Patches build_chat_agent + stream_chat_response.

    stream_chat_response is now an async context manager that yields an async
    iterator of tokens.  The mock must match that contract.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_stream_cm(*_a, **_kw):
        async def _tokens():
            yield token
        yield _tokens()

    mock_agent = MagicMock()
    return (
        patch("app.api.websocket.build_chat_agent", return_value=mock_agent),
        patch("app.api.websocket.stream_chat_response", new=_fake_stream_cm),
    )


# ── Connection tests ─────────────────────────────────────────────────────────

def test_ws_connect_valid_session_receives_greeting(
    client_with_session: TestClient, valid_session_id: str
) -> None:
    """First message after connect must be a greeting token."""
    p1, p2 = _patch_chat_agent()
    with p1, p2:
        with client_with_session.websocket_connect(f"/v1/ws/{valid_session_id}") as ws:
            greeting = ws.receive_json()
            assert greeting["type"] == WsMessageType.TOKEN
            assert len(greeting["content"]) > 5


def test_ws_connect_expired_session_closes_4003(
    client_expired_session: TestClient, valid_session_id: str
) -> None:
    with pytest.raises(Exception):
        with client_expired_session.websocket_connect(f"/v1/ws/{valid_session_id}") as ws:
            ws.receive_json()


def test_ws_connect_unknown_session_closes_4004(client_no_session: TestClient) -> None:
    with pytest.raises(Exception):
        with client_no_session.websocket_connect("/v1/ws/unknown-session-id") as ws:
            ws.receive_json()


def test_ws_send_message_receives_token_response(
    client_with_session: TestClient, valid_session_id: str
) -> None:
    """After sending a user message, the agent response token arrives."""
    from contextlib import asynccontextmanager
    call_count = 0

    @asynccontextmanager
    async def _fake_stream(*_a, **_kw):
        nonlocal call_count
        call_count += 1
        async def _tokens():
            yield "Oto przepis na pastę!"
        yield _tokens()

    mock_agent = MagicMock()
    with (
        patch("app.api.websocket.build_chat_agent", return_value=mock_agent),
        patch("app.api.websocket.stream_chat_response", new=_fake_stream),
    ):
        with client_with_session.websocket_connect(f"/v1/ws/{valid_session_id}") as ws:
            ws.receive_json()  # greeting token
            ws.send_text('{"type":"message","content":"zrób mi pastę"}')
            reply = ws.receive_json()
            assert reply["type"] == WsMessageType.TOKEN
            assert "pasta" in reply["content"].lower() or "przepis" in reply["content"].lower()

    assert call_count == 1


# ── Spiżarnia toggle tests ────────────────────────────────────────────────────

_SPIZ_UID = "spiz-user-001"
_SPIZARNIA = Spizarnia(
    uid=_SPIZ_UID,
    items=[
        SpizarniaItem(name="kurczak", quantity="2 piersi", added_at=datetime.now(UTC)),
        SpizarniaItem(name="szpinak", quantity="", added_at=datetime.now(UTC)),
    ],
)


@pytest.fixture()
def client_spiz_session(valid_session_id: str) -> TestClient:
    app.state.settings = _make_mock_settings()
    mock_fs = _make_mock_firestore(session=_make_session(valid_session_id, uid=None))
    mock_fs.get_spizarnia = AsyncMock(return_value=_SPIZARNIA)
    app.state.firestore = mock_fs
    app.dependency_overrides[get_tenant_config] = lambda: _test_config

    with (
        patch("app.middleware.auth._get_firebase_app"),
        patch("firebase_admin.auth.verify_id_token", return_value={"uid": _SPIZ_UID}),
    ):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    app.dependency_overrides.clear()
    del app.state.settings
    del app.state.firestore


def test_ws_spizarnia_toggle_sends_announcement(
    client_spiz_session: TestClient, valid_session_id: str
) -> None:
    """With ?use_spizarnia=true the server sends a spiżarnia announcement token."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_stream(*_a, **_kw):
        async def _tokens():
            yield "Tutaj przepis!"
        yield _tokens()

    mock_agent = MagicMock()
    received: list[dict] = []

    with (
        patch("app.api.websocket.build_chat_agent", return_value=mock_agent),
        patch("app.api.websocket.stream_chat_response", new=_fake_stream),
    ):
        url = f"/v1/ws/{valid_session_id}?use_spizarnia=true"
        headers = {"authorization": f"Bearer token-for-{_SPIZ_UID}"}
        with client_spiz_session.websocket_connect(url, headers=headers) as ws:
            for _ in range(5):
                msg = ws.receive_json()
                received.append(msg)

    contents = [m.get("content", "") for m in received]
    assert any("spiżarni" in c.lower() or "kurczak" in c.lower() for c in contents), (
        "Expected spiżarnia announcement mentioning pantry items"
    )
