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


def build_shopping_list_agent(config: TenantConfig) -> Agent[None, ShoppingList]:
    return Agent(
        config.model_shopping_list,
        output_type=ShoppingList,
        defer_model_check=True,
        instructions=f"""You are a shopping list organiser. Your job is to process a raw list of
ingredient strings from multiple recipes and return a structured shopping list.

Rules:
1. Deduplicate — if the same ingredient appears multiple times, merge into one item.
2. Sum quantities — "200g mąki" + "300g mąki" → "500g mąki". Keep the unit consistent.
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
7. Respond in {config.language}. Item names should be in {config.language}.""",
    )
