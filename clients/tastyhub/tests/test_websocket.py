import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import app
from app.middleware.auth import get_tenant_config
from cookbot.models.recipe import ParsedIngredients, Recipe, UserIntent
from cookbot.models.session import Session, SessionStatus
from cookbot.models.tenant import TenantConfig
from cookbot.protocols.ws_messages import WsMessageType

from datetime import UTC, datetime, timedelta

TEST_TENANT_ID = "tastyhub"
TEST_API_KEY = "tk_test_key"

_test_config = TenantConfig(
    tenant_id=TEST_TENANT_ID,
    persona="Test chef",
    language="en",
    recipe_source_url="",
    allowed_origins=[],
)

_INTENT = UserIntent(
    dish_type="pasta",
    servings=2,
    max_time_minutes=30,
    available_ingredients=["pasta"],
    free_notes="",
)

_INGREDIENTS = ParsedIngredients(
    items=["pasta"],
    must_use=[],
    dietary_hints=[],
    missing_staples=[],
)


def _make_session(session_id: str, *, expired: bool = False) -> Session:
    now = datetime.now(UTC)
    expires_at = now - timedelta(hours=1) if expired else now + timedelta(hours=24)
    return Session(
        session_id=session_id,
        tenant_id=TEST_TENANT_ID,
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
    s.openai_model = "gpt-4o-mini"
    s.max_hitl_rounds = 3
    s.firestore_emulator_host = ""
    return s


def _agent_mock(output) -> MagicMock:
    result = MagicMock()
    result.output = output
    agent = AsyncMock()
    agent.run = AsyncMock(return_value=result)
    return agent


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


# ── WebSocket connection tests ────────────────────────────────────────────────

def test_ws_connect_valid_session_receives_greeting(
    client_with_session: TestClient, valid_session_id: str
) -> None:
    """First message after connect must be a greeting token."""
    with client_with_session.websocket_connect(f"/v1/ws/{valid_session_id}") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == WsMessageType.TOKEN
        assert len(greeting["content"]) > 10  # non-empty greeting
        first_question = ws.receive_json()
        assert first_question["type"] == WsMessageType.TOKEN
        assert len(first_question["content"]) > 10  # non-empty first question


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


def test_ws_intake_five_questions_sent(
    client_with_session: TestClient, valid_session_id: str
) -> None:
    """Server sends exactly 5 questions and waits for an answer between each."""
    with client_with_session.websocket_connect(f"/v1/ws/{valid_session_id}") as ws:
        ws.receive_json()  # greeting

        for i in range(5):
            q = ws.receive_json()
            assert q["type"] == WsMessageType.TOKEN
            assert len(q["content"]) > 10
            ws.send_text(f'{{"type":"message","content":"answer {i}"}}')


def test_ws_full_pipeline_final_recipe_delivered(
    client_with_session: TestClient, valid_session_id: str
) -> None:
    """After answering all 5 questions, the final recipe message arrives."""
    recipe = Recipe(
        name="Test Pasta",
        description="A simple pasta.",
        ingredients=["pasta", "tomato"],
        steps=["Boil pasta.", "Add sauce.", "Serve."],
        prep_time_minutes=5,
        cook_time_minutes=20,
        difficulty="Easy",
        servings=2,
        tips=[],
    )

    _intake_module = "app.api.websocket.build_intake_agent"
    _ingredient_module = "app.api.websocket.build_ingredient_agent"
    _orchestrator_module = "app.api.websocket.SessionOrchestrator"

    from cookbot.models.recipe import RecipeSource

    async def fake_orchestrator_run(**kwargs) -> None:
        await kwargs["on_final_recipe"](recipe, RecipeSource.WEB_SEARCH)

    mock_orchestrator = MagicMock()
    mock_orchestrator.run = AsyncMock(side_effect=fake_orchestrator_run)

    with (
        patch(_intake_module, return_value=_agent_mock(_INTENT)),
        patch(_ingredient_module, return_value=_agent_mock(_INGREDIENTS)),
        patch(_orchestrator_module, return_value=mock_orchestrator),
    ):
        with client_with_session.websocket_connect(f"/v1/ws/{valid_session_id}") as ws:
            ws.receive_json()  # greeting

            answers = ["pasta", "2", "30 minutes", "spinach", "no"]
            for answer in answers:
                ws.receive_json()  # question
                ws.send_text(f'{{"type":"message","content":"{answer}"}}')

            ws.receive_json()  # "Got it! Let me work out..."
            ws.receive_json()  # "Understood! Dish: ..."

            final = ws.receive_json()
            assert final["type"] == WsMessageType.FINAL_RECIPE
            assert final["recipe"]["name"] == "Test Pasta"
