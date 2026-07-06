from __future__ import annotations

from pydantic_ai import Agent

from cookbot.agents.measures import convert_measure
from cookbot.models.shopping import ShoppingList
from cookbot.models.tenant import TenantConfig

SECTIONS_ORDER = [
    "warzywa/owoce",
    "nabiał",
    "mięso/ryby",
    "piekarnia",
    "suche produkty",
    "inne",
]


def shopping_list_instructions(config: TenantConfig) -> str:
    return f"""You are a shopping list organiser. Your job is to process a raw list of
shopping items (from recipes and/or typed by the user) and return a structured
shopping list. Items may be anything a person buys — NOT only cooking ingredients
(e.g. "baterie AA", "worki na śmieci", "znaczki"). Keep every input item.

Rules:
0. NEVER drop an item. Every input line must appear in the output exactly once
   (after any valid same-item merges). If an item is not food/cooking-related and
   fits no other section, put it in "inne" — never discard it.
1. Deduplicate ONLY truly identical products — merge two entries only when they are
   the SAME product. Different product qualifiers make DIFFERENT items and must stay
   on SEPARATE lines: fat/percentage ("śmietanka 30%" vs "śmietana 18%"), type
   ("śmietanka" vs "śmietana", "masło" vs "masło klarowane"), and variety/cut
   ("papryka czerwona" vs "papryka zielona", "cukier" vs "cukier trzcinowy").
   Never generalise a qualified product to its generic name to force a merge —
   keep the qualifier in the item name.
2. Normalise units to standard measures — grams (g), millilitres (ml), or piece
   counts (szt.). For any amount given in szklanka/łyżka/łyżeczka you MUST call the
   `convert_measure` tool to get the exact value — never do this conversion in your
   head (e.g. "1/3 szklanki" is 80 ml, NOT 150 ml). Use the tool's returned string.
   Leave amounts that are already metric (g/ml) or piece counts as they are.
3. Sum quantities — "200g mąki" + "300g mąki" → "500g mąki". Keep the unit consistent.
   Only sum quantities of items you actually merged in rule 1 (same product).
   Convert both to the same unit (via the tool) before summing when needed.
   If quantities cannot be summed (e.g. "2 duże cebule" + "1 cebula"), write a sensible combined quantity.
   If no quantity is specified, use "wg uznania".
4. Assign each item to exactly one section from this fixed list (in order):
   - warzywa/owoce  (vegetables, fruit, herbs, mushrooms)
   - nabiał         (milk, cheese, butter, eggs, yogurt, cream)
   - mięso/ryby     (meat, poultry, fish, seafood, cold cuts)
   - piekarnia      (bread, rolls, flour-based baked goods)
   - suche produkty (pasta, rice, grains, canned goods, spices, oil, sugar, salt, dry beans)
   - inne           (anything that doesn't fit above)
5. Within each section sort items alphabetically by name.
6. Return only sections that have at least one item.
7. The `sections` field must list only the section names present, in the order above.
8. Respond in {config.language}. Item names should be in {config.language}."""


def build_shopping_list_agent(config: TenantConfig) -> Agent[None, ShoppingList]:
    agent = Agent(
        config.model_shopping_list,
        output_type=ShoppingList,
        defer_model_check=True,
        instructions=shopping_list_instructions(config),
    )

    @agent.tool_plain
    def convert_measure_tool(amount: str, to_grams: bool = False) -> str:
        """Convert a Polish volumetric cooking amount (szklanka/łyżka/łyżeczka) to a
        standard unit — millilitres, or grams when to_grams=True (for łyżka/łyżeczka).

        Call this for EVERY amount expressed in szklanka/łyżka/łyżeczka so the value
        is exact (e.g. "1/3 szklanki" → "80 ml"). Returns the original amount
        unchanged if it has no convertible unit (already g/ml, or a piece count).
        """
        return convert_measure(amount, to_grams=to_grams) or amount

    return agent
