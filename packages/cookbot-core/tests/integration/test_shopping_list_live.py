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


def _quantities(result) -> list[str]:
    return [item.quantity.lower() for item in result.output.items]


async def test_szklanka_amount_is_converted_via_tool(pl_config) -> None:
    """The agent must convert "1/3 szklanki" to the exact 80 ml (not 150) by calling
    the deterministic tool, not doing the arithmetic itself."""
    agent = build_shopping_list_agent(pl_config)
    result = await agent.run("1/3 szklanki śmietanki 30%\n2 łyżki cukru")
    qtys = " | ".join(_quantities(result))

    assert "80 ml" in qtys, f"expected '1/3 szklanki' → 80 ml, got: {qtys}"
    # The known wrong value the LLM produced before must not appear.
    assert "150 ml" not in qtys, f"stale 150 ml conversion resurfaced: {qtys}"
    assert "30 g" in qtys, f"expected '2 łyżki' → 30 g, got: {qtys}"


async def test_non_food_items_are_kept_in_inne(pl_config) -> None:
    """Manually-added items may be anything — non-food items must survive the
    organize step (dropped-item bug), landing in the "inne" section."""
    agent = build_shopping_list_agent(pl_config)
    result = await agent.run("mleko\nbaterie AA\nworki na śmieci")
    names = _names(result)

    assert any("bateri" in n for n in names), f"'baterie AA' was dropped: {names}"
    assert any("worki" in n or "śmiec" in n or "smiec" in n for n in names), (
        f"'worki na śmieci' was dropped: {names}"
    )
