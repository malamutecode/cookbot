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


def _section_of(result, needle: str) -> str | None:
    for item in result.output.items:
        if needle in item.name.lower():
            return item.section
    return None


async def test_items_get_correct_supermarket_aisle(pl_config) -> None:
    """The reported bug: czosnek landed in "inne" and household items had no home.
    With the aisle taxonomy, items must be assigned to the aisle that sells them."""
    agent = build_shopping_list_agent(pl_config)
    result = await agent.run(
        "czosnek 4 szt\npapier toaletowy\nręcznik papierowy\nmleko\nchleb\npiwo"
    )
    names = _names(result)

    # Nothing dropped.
    for needle in ("czosnek", "papier toaletowy", "ręcznik", "mleko", "chleb", "piwo"):
        assert any(needle.split()[0] in n for n in names), f"{needle!r} dropped: {names}"

    # Garlic is produce, never "inne" (the reported #1 bug).
    assert _section_of(result, "czosnek") == "warzywa/owoce"
    # Household paper goes to the chemia/dom aisle, not "inne" (reported #2 bug).
    assert _section_of(result, "papier toaletowy") == "chemia/dom"
    assert _section_of(result, "ręcznik") == "chemia/dom"
