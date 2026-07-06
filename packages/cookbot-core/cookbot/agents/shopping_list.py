from __future__ import annotations

from pydantic_ai import Agent

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
ingredient strings from multiple recipes and return a structured shopping list.

Rules:
1. Deduplicate ONLY truly identical products — merge two entries only when they are
   the SAME product. Different product qualifiers make DIFFERENT items and must stay
   on SEPARATE lines: fat/percentage ("śmietanka 30%" vs "śmietana 18%"), type
   ("śmietanka" vs "śmietana", "masło" vs "masło klarowane"), and variety/cut
   ("papryka czerwona" vs "papryka zielona", "cukier" vs "cukier trzcinowy").
   Never generalise a qualified product to its generic name to force a merge —
   keep the qualifier in the item name.
2. Sum quantities — "200g mąki" + "300g mąki" → "500g mąki". Keep the unit consistent.
   Only sum quantities of items you actually merged in rule 1 (same product).
   If quantities cannot be summed (e.g. "2 duże cebule" + "1 cebula"), write a sensible combined quantity.
   If no quantity is specified, use "wg uznania".
3. Assign each item to exactly one section from this fixed list (in order):
   - warzywa/owoce  (vegetables, fruit, herbs, mushrooms)
   - nabiał         (milk, cheese, butter, eggs, yogurt, cream)
   - mięso/ryby     (meat, poultry, fish, seafood, cold cuts)
   - piekarnia      (bread, rolls, flour-based baked goods)
   - suche produkty (pasta, rice, grains, canned goods, spices, oil, sugar, salt, dry beans)
   - inne           (anything that doesn't fit above)
4. Within each section sort items alphabetically by name.
5. Return only sections that have at least one item.
6. The `sections` field must list only the section names present, in the order above.
7. Respond in {config.language}. Item names should be in {config.language}."""


def build_shopping_list_agent(config: TenantConfig) -> Agent[None, ShoppingList]:
    return Agent(
        config.model_shopping_list,
        output_type=ShoppingList,
        defer_model_check=True,
        instructions=shopping_list_instructions(config),
    )
