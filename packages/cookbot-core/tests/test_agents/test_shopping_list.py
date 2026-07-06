"""Tests for the shopping-list agent.

Unit tests are prompt-content guards (the actual dedup/merge is done by the LLM,
covered live under -m integration). They lock in the qualifier-preservation rule
so a future prompt edit can't silently reintroduce the "śmietanka 30% → śmietana"
merge bug.
"""

from __future__ import annotations

from cookbot.agents.shopping_list import shopping_list_instructions
from cookbot.models.tenant import TenantConfig

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
