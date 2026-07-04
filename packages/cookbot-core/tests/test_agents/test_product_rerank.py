"""Unit tests for the product re-rank agent — no real LLM (uses TestModel)."""

from __future__ import annotations

from pydantic_ai import models
from pydantic_ai.models.test import TestModel

from cookbot.agents.product_rerank import ReRankChoice, build_product_rerank_agent
from cookbot.models.tenant import TenantConfig

models.ALLOW_MODEL_REQUESTS = False

_CONFIG = TenantConfig(
    tenant_id="t",
    persona="chef",
    language="pl",
    recipe_source_url="",
    allowed_origins=[],
)


async def test_returns_structured_choice() -> None:
    agent = build_product_rerank_agent(_CONFIG)
    with agent.override(model=TestModel(custom_output_args={"choice": 2})):
        result = await agent.run("Ingredient: masło\nCandidates:\n1. A\n2. B")
    assert isinstance(result.output, ReRankChoice)
    assert result.output.choice == 2


async def test_can_decline_with_null() -> None:
    agent = build_product_rerank_agent(_CONFIG)
    with agent.override(model=TestModel(custom_output_args={"choice": None})):
        result = await agent.run("Ingredient: xyz\nCandidates:\n1. A")
    assert result.output.choice is None
