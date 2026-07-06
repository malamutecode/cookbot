"""Tests for the shopping-list agent.

Unit tests are prompt-content guards (the actual dedup/merge is done by the LLM,
covered live under -m integration). They lock in the qualifier-preservation rule
so a future prompt edit can't silently reintroduce the "śmietanka 30% → śmietana"
merge bug.
"""

from __future__ import annotations

from pydantic_ai import models

from cookbot.agents.shopping_list import build_shopping_list_agent, shopping_list_instructions
from cookbot.models.tenant import TenantConfig

models.ALLOW_MODEL_REQUESTS = False

_CONFIG = TenantConfig(
    tenant_id="t",
    persona="chef",
    language="pl",
    recipe_source_url="",
    allowed_origins=[],
)


def test_prompt_forbids_generalising_qualified_products() -> None:
    text = shopping_list_instructions(_CONFIG)
    # The rule must name the canonical products that broke and require them to stay
    # on separate lines rather than being generalised.
    assert "śmietanka 30%" in text
    assert "SEPARATE lines" in text
    assert "generalise" in text.lower()


def test_prompt_requires_measure_conversion_tool() -> None:
    text = shopping_list_instructions(_CONFIG)
    assert "convert_measure" in text
    assert "80 ml" in text  # the corrected 1/3 szklanki value is spelled out


def test_measure_conversion_tool_is_registered() -> None:
    agent = build_shopping_list_agent(_CONFIG)
    tool_names = set(agent._function_toolset.tools)
    assert "convert_measure_tool" in tool_names
