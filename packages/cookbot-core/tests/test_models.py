from datetime import UTC, datetime, timedelta

import pytest

from cookbot.hitl.models import HITLCheckpoint, HITLOutcome, HITLResponse
from cookbot.models.recipe import ParsedIngredients, Recipe, RecipeSearchResult, RecipeSource
from cookbot.models.session import Message, Session, SessionStatus
from cookbot.models.tenant import TenantConfig
from cookbot.protocols.ws_messages import (
    WsInbound,
    WsMessageType,
    WsOutAgentUpdate,
    WsOutError,
    WsOutFinalRecipe,
    WsOutHitlCheckpoint,
    WsOutHitlLabels,
    WsOutToken,
)


def _sample_recipe() -> Recipe:
    return Recipe(
        name="Garlic Chicken",
        description="Quick weeknight chicken.",
        ingredients=["2 chicken breasts", "4 garlic cloves", "1 tbsp olive oil"],
        steps=["Heat oil.", "Cook chicken 6 min per side.", "Add garlic, cook 2 min."],
        prep_time_minutes=5,
        cook_time_minutes=15,
        difficulty="Easy",
        servings=2,
        tips=["Let chicken rest before slicing."],
    )


# ── TenantConfig ──────────────────────────────────────────────────────────────

def test_tenant_config_instantiation() -> None:
    config = TenantConfig(
        tenant_id="test",
        persona="You are a chef.",
        language="en",
        recipe_source_url="https://example.com/sitemap.xml",
        allowed_origins=["https://example.com"],
    )
    assert config.tenant_id == "test"
    assert config.model_chat == "gpt-4o-mini"
    assert config.max_hitl_rounds == 3
    assert config.feature_nutrition is False


# ── ParsedIngredients ─────────────────────────────────────────────────────────

def test_parsed_ingredients_instantiation() -> None:
    pi = ParsedIngredients(
        items=["chicken", "spinach", "courgette"],
        must_use=["courgette"],
        dietary_hints=["gluten-free"],
        missing_staples=["salt", "olive oil"],
    )
    assert len(pi.items) == 3
    assert pi.must_use == ["courgette"]
    assert "gluten-free" in pi.dietary_hints


def test_parsed_ingredients_must_use_defaults_empty() -> None:
    pi = ParsedIngredients(
        items=["eggs", "cheese"],
        must_use=[],
        dietary_hints=[],
        missing_staples=[],
    )
    assert pi.must_use == []


# ── Recipe ────────────────────────────────────────────────────────────────────

def test_recipe_instantiation() -> None:
    recipe = _sample_recipe()
    assert recipe.name == "Garlic Chicken"
    assert len(recipe.steps) >= 3
    assert recipe.prep_time_minutes > 0
    assert recipe.difficulty in ("Easy", "Medium", "Hard")


def test_recipe_serialisation() -> None:
    recipe = _sample_recipe()
    data = recipe.model_dump()
    assert data["name"] == "Garlic Chicken"
    restored = Recipe.model_validate(data)
    assert restored == recipe


# ── RecipeSearchResult ────────────────────────────────────────────────────────

def test_recipe_search_result() -> None:
    result = RecipeSearchResult(
        recipe=_sample_recipe(),
        source=RecipeSource.TENANT_KB,
        similarity_score=0.92,
    )
    assert result.source == RecipeSource.TENANT_KB
    assert 0.0 <= result.similarity_score <= 1.0


# ── Message / Session ─────────────────────────────────────────────────────────

def test_message_defaults_timestamp() -> None:
    msg = Message(role="user", content="Hello")
    assert isinstance(msg.timestamp, datetime)


def test_session_instantiation() -> None:
    now = datetime.now(UTC)
    session = Session(
        session_id="sess-123",
        tenant_id="tastyhub",
        expires_at=now + timedelta(hours=24),
    )
    assert session.status == SessionStatus.ACTIVE
    assert session.messages == []


# ── HITL models ───────────────────────────────────────────────────────────────

def test_hitl_checkpoint_instantiation() -> None:
    cp = HITLCheckpoint(
        checkpoint_id="cp-1",
        session_id="sess-123",
        recipe=_sample_recipe(),
        round_number=1,
        created_at=datetime.now(UTC),
    )
    assert cp.round_number == 1
    assert cp.recipe.name == "Garlic Chicken"


def test_hitl_response_approved() -> None:
    resp = HITLResponse(approved=True)
    assert resp.modification is None


def test_hitl_response_modified() -> None:
    resp = HITLResponse(approved=False, modification="make it vegan")
    assert resp.modification == "make it vegan"


def test_hitl_outcome_values() -> None:
    assert HITLOutcome.APPROVED == "APPROVED"
    assert HITLOutcome.MODIFIED == "MODIFIED"
    assert HITLOutcome.REJECTED == "REJECTED"


# ── WS message models ─────────────────────────────────────────────────────────

def test_ws_out_token() -> None:
    msg = WsOutToken(content="Let me check...")
    assert msg.type == WsMessageType.TOKEN
    assert '"type":"token"' in msg.model_dump_json()


def test_ws_out_agent_update() -> None:
    msg = WsOutAgentUpdate(agent="IngredientAgent", status="running")
    assert msg.type == WsMessageType.AGENT_UPDATE


def test_ws_out_hitl_checkpoint() -> None:
    labels = WsOutHitlLabels(
        heading="Round {round}: ok?", approve="Approve", modify="Modify", reject="Reject",
        modify_placeholder="What?", modify_send="Send",
        approved_note="Approved", rejected_note="Rejected", modification_note='Mod: "{text}"',
    )
    msg = WsOutHitlCheckpoint(recipe=_sample_recipe(), round=1, labels=labels)
    assert msg.type == WsMessageType.HITL_CHECKPOINT


def test_ws_out_final_recipe() -> None:
    msg = WsOutFinalRecipe(recipe=_sample_recipe(), source=RecipeSource.AI_GENERATED)
    assert msg.type == WsMessageType.FINAL_RECIPE
    assert msg.source == RecipeSource.AI_GENERATED


def test_ws_out_error() -> None:
    msg = WsOutError(message="Something went wrong")
    assert msg.type == WsMessageType.ERROR


def test_ws_inbound_message() -> None:
    msg = WsInbound(type=WsMessageType.MESSAGE, content="I have eggs")
    assert msg.content == "I have eggs"
    assert msg.approved is None


def test_ws_inbound_hitl_response_approve() -> None:
    msg = WsInbound(type=WsMessageType.HITL_RESPONSE, approved=True)
    assert msg.approved is True
    assert msg.modification is None


def test_ws_inbound_hitl_response_modify() -> None:
    msg = WsInbound(
        type=WsMessageType.HITL_RESPONSE,
        approved=False,
        modification="less spicy",
    )
    assert msg.approved is False
    assert msg.modification == "less spicy"
