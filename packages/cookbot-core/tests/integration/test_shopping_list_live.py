"""Live shopping-list agent behaviour (real LLM).

Guards the qualifier-preservation fix: differently-qualified products (e.g.
śmietanka 30% vs śmietana 18%) must NOT be merged into one generic "śmietana"
line. Opt-in (`-m integration`); auto-skips without OPENAI_API_KEY.
"""

from __future__ import annotations

from cookbot.agents.shopping_list import build_shopping_list_agent


def _names(result) -> list[str]:
    return [item.name.lower() for item in result.output.items]


async def test_different_cream_qualifiers_stay_separate(pl_config) -> None:
    agent = build_shopping_list_agent(pl_config)
    result = await agent.run(
        "\n".join(
            [
                "1/3 szklanki śmietanki 30%",
                "200 ml śmietany 18%",
                "150 g makaronu",
            ]
        )
    )
    names = _names(result)

    # Both cream products must survive as distinct items — not collapsed to one.
    has_30 = any("30" in n for n in names)
    has_18 = any("18" in n for n in names)
    assert has_30 and has_18, (
        f"cream qualifiers lost — expected both 30% and 18% cream as separate "
        f"items, got: {names}"
    )
    # And there must be at least two cream lines, not a single merged "śmietana".
    cream_lines = [n for n in names if "smietan" in n or "śmietan" in n]
    assert len(cream_lines) >= 2, f"creams were merged: {names}"
