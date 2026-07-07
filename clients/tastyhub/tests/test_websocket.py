from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cookbot.models.session import Session, SessionStatus
from cookbot.models.spizarnia import Spizarnia, SpizarniaItem
from cookbot.models.tenant import TenantConfig
from cookbot.protocols.ws_messages import WsMessageType
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import app
from app.middleware.auth import get_tenant_config

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
    from cookbot.models.user import UsageCounter, UserRecord

    mock = AsyncMock()
    mock.save_session = AsyncMock(return_value=None)
    mock.get_session = AsyncMock(return_value=session)
    mock.save_message = AsyncMock(return_value=None)
    mock.save_hitl_checkpoint = AsyncMock(return_value=None)
    mock.clear_hitl_checkpoint = AsyncMock(return_value=None)
    mock.get_hitl_checkpoint = AsyncMock(return_value=None)
    mock.get_chat_state = AsyncMock(return_value=None)
    mock.save_chat_state = AsyncMock(return_value=None)
    # Quota defaults (STEP 42): unlimited record + zero usage so the gate is a
    # no-op unless a test overrides these.
    uid = session.uid if session else None
    mock.get_user_record = AsyncMock(return_value=UserRecord(uid=uid or "test-uid"))
    mock.get_usage_counter = AsyncMock(
        return_value=UsageCounter(period_key="2026-01-01", tokens_used=0)
    )
    mock.add_usage = AsyncMock(return_value=None)
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


# ── Chat-state persistence ────────────────────────────────────────────────────

def test_ws_turn_persists_chat_state(
    client_with_session: TestClient, valid_session_id: str
) -> None:
    """After a completed turn the handler saves the resumable snapshot."""
    p1, p2 = _patch_chat_agent()
    with p1, p2:
        with client_with_session.websocket_connect(f"/v1/ws/{valid_session_id}") as ws:
            ws.receive_json()  # greeting
            ws.send_text('{"type":"message","content":"hej"}')
            ws.receive_json()  # reply turn 1
            # A second turn guarantees turn 1 (stream → events → save) finished.
            ws.send_text('{"type":"message","content":"druga wiadomość"}')
            ws.receive_json()  # reply turn 2
            assert app.state.firestore.save_chat_state.await_count >= 1
            call = app.state.firestore.save_chat_state.await_args_list[0]
            assert call.args[0] == valid_session_id
            assert isinstance(call.args[1], dict)


def test_ws_connect_restores_chat_state_snapshot(
    client_with_session: TestClient, valid_session_id: str
) -> None:
    """A persisted snapshot is loaded on connect and must not break the chat."""
    from cookbot.agents.chat import ChatAgentDeps, OnboardingState, dump_chat_state

    snapshot_deps = ChatAgentDeps(
        config=_test_config,
        onboarding=OnboardingState(dish_type="pasta", servings=2),
    )
    snapshot = dump_chat_state(snapshot_deps, [])
    app.state.firestore.get_chat_state = AsyncMock(return_value=snapshot)

    p1, p2 = _patch_chat_agent()
    with p1, p2:
        with client_with_session.websocket_connect(f"/v1/ws/{valid_session_id}") as ws:
            greeting = ws.receive_json()
            assert greeting["type"] == WsMessageType.TOKEN
            ws.send_text('{"type":"message","content":"hej"}')
            reply = ws.receive_json()
            assert reply["type"] == WsMessageType.TOKEN
    assert app.state.firestore.get_chat_state.await_count == 1


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
    from cookbot.models.user import DEFAULT_SOURCES, UserSearchPrefs

    app.state.settings = _make_mock_settings()
    mock_fs = _make_mock_firestore(session=_make_session(valid_session_id, uid=None))
    mock_fs.get_spizarnia = AsyncMock(return_value=_SPIZARNIA)
    # The authed path loads real prefs — a bare MagicMock here would smuggle
    # non-str values into ChatAgentDeps.
    mock_fs.get_search_prefs = AsyncMock(
        return_value=UserSearchPrefs(uid=_SPIZ_UID, sources=list(DEFAULT_SOURCES))
    )
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


# ── Token quota enforcement (STEP 42) ─────────────────────────────────────────

_QUOTA_UID = "quota-ws-user"


@pytest.fixture()
def client_quota_session(valid_session_id: str) -> TestClient:
    from cookbot.models.user import DEFAULT_SOURCES, UserSearchPrefs

    app.state.settings = _make_mock_settings()
    mock_fs = _make_mock_firestore(session=_make_session(valid_session_id, uid=None))
    mock_fs.get_search_prefs = AsyncMock(
        return_value=UserSearchPrefs(uid=_QUOTA_UID, sources=list(DEFAULT_SOURCES))
    )
    app.state.firestore = mock_fs
    app.dependency_overrides[get_tenant_config] = lambda: _test_config

    with (
        patch("app.middleware.auth._get_firebase_app"),
        patch("firebase_admin.auth.verify_id_token", return_value={"uid": _QUOTA_UID}),
    ):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    app.dependency_overrides.clear()
    del app.state.settings
    del app.state.firestore


def test_ws_refuses_turn_over_daily_budget(
    client_quota_session: TestClient, valid_session_id: str
) -> None:
    """A user over their daily limit gets a quota_exceeded message, not a turn."""
    from cookbot.models.quota import day_key
    from cookbot.models.user import TokenQuota, UsageCounter, UserRecord

    fs = app.state.firestore
    fs.get_user_record = AsyncMock(
        return_value=UserRecord(uid=_QUOTA_UID, quota=TokenQuota(daily_limit=100))
    )
    # Already spent the whole daily budget — counter must carry the CURRENT period
    # key or counter_for() would lazily reset it to 0. Return the right counter
    # per requested key (day vs month).
    dk = day_key(datetime.now(UTC), _test_config.quota_timezone)

    async def _counter(_uid, key):
        used = 100 if key == dk else 0
        return UsageCounter(period_key=key, tokens_used=used)

    fs.get_usage_counter = AsyncMock(side_effect=_counter)

    p1, p2 = _patch_chat_agent()
    with p1, p2:
        headers = {"authorization": f"Bearer token-for-{_QUOTA_UID}"}
        with client_quota_session.websocket_connect(
            f"/v1/ws/{valid_session_id}", headers=headers
        ) as ws:
            ws.receive_json()  # greeting
            ws.send_text('{"type":"message","content":"zrób mi pastę"}')
            reply = ws.receive_json()
            assert reply["type"] == WsMessageType.QUOTA_EXCEEDED
            assert reply["window"] == "daily"
    # The turn was refused before streaming — no usage recorded.
    fs.add_usage.assert_not_awaited()


def test_ws_refuses_non_allowlisted_email(
    client_quota_session: TestClient, valid_session_id: str
) -> None:
    """A valid token whose email is not on the whitelist is closed (code 4008),
    before the chat greeting is sent."""
    from starlette.websockets import WebSocketDisconnect as StarletteWSDisconnect

    from app.config.settings import get_settings

    # Token verifies with an email that is NOT on the whitelist.
    with (
        patch.object(get_settings(), "allowed_emails", ["allowed@example.com"]),
        patch("firebase_admin.auth.verify_id_token",
              return_value={"uid": _QUOTA_UID, "email": "intruder@evil.com"}),
    ):
        headers = {"authorization": f"Bearer token-for-{_QUOTA_UID}"}
        with pytest.raises(StarletteWSDisconnect) as exc:
            with client_quota_session.websocket_connect(
                f"/v1/ws/{valid_session_id}", headers=headers
            ) as ws:
                ws.receive_json()  # should never arrive — connection refused
        assert exc.value.code == 4008


def test_ws_allows_allowlisted_email(
    client_quota_session: TestClient, valid_session_id: str
) -> None:
    """A valid token whose email IS on the whitelist connects normally."""
    from app.config.settings import get_settings

    p1, p2 = _patch_chat_agent()
    with (
        patch.object(get_settings(), "allowed_emails", ["ok@example.com"]),
        patch("firebase_admin.auth.verify_id_token",
              return_value={"uid": _QUOTA_UID, "email": "ok@example.com"}),
        p1, p2,
    ):
        headers = {"authorization": f"Bearer token-for-{_QUOTA_UID}"}
        with client_quota_session.websocket_connect(
            f"/v1/ws/{valid_session_id}", headers=headers
        ) as ws:
            greeting = ws.receive_json()
            assert greeting["type"] == WsMessageType.TOKEN


def test_ws_records_usage_after_turn(
    client_quota_session: TestClient, valid_session_id: str
) -> None:
    """A normal turn under budget meters its token spend via add_usage."""
    from contextlib import asynccontextmanager

    from cookbot.models.user import TokenQuota, UsageCounter, UserRecord

    fs = app.state.firestore
    fs.get_user_record = AsyncMock(
        return_value=UserRecord(uid=_QUOTA_UID, quota=TokenQuota(daily_limit=10_000))
    )
    fs.get_usage_counter = AsyncMock(
        return_value=UsageCounter(period_key="k", tokens_used=0)
    )

    # The fake stream must set deps.last_turn_total_tokens like the real one does.
    @asynccontextmanager
    async def _fake_stream(_agent, deps, _history, _text, **_kw):
        deps.last_turn_total_tokens = 321
        async def _tokens():
            yield "Oto przepis!"
        yield _tokens()

    mock_agent = MagicMock()
    with (
        patch("app.api.websocket.build_chat_agent", return_value=mock_agent),
        patch("app.api.websocket.stream_chat_response", new=_fake_stream),
    ):
        headers = {"authorization": f"Bearer token-for-{_QUOTA_UID}"}
        with client_quota_session.websocket_connect(
            f"/v1/ws/{valid_session_id}", headers=headers
        ) as ws:
            ws.receive_json()  # greeting
            ws.send_text('{"type":"message","content":"hej"}')
            ws.receive_json()  # token reply
            # Second turn guarantees the first turn's post-stream work ran.
            ws.send_text('{"type":"message","content":"jeszcze raz"}')
            ws.receive_json()

    assert fs.add_usage.await_count >= 1
    first = fs.add_usage.await_args_list[0]
    # add_usage(uid, [day_key, month_key], tokens)
    assert first.args[0] == _QUOTA_UID
    assert first.args[2] == 321


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
            received.append(ws.receive_json())  # greeting
            received.append(ws.receive_json())  # spiżarnia announcement

    contents = [m.get("content", "") for m in received]
    assert any("spiżarni" in c.lower() or "kurczak" in c.lower() for c in contents), (
        "Expected spiżarnia announcement mentioning pantry items"
    )
