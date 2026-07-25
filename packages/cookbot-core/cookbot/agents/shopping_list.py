from __future__ import annotations

from pydantic_ai import Agent

from cookbot.models.measures import convert_measure
from cookbot.models.shopping import ShoppingList
from cookbot.models.tenant import TenantConfig

# Supermarket-aisle taxonomy — the goal is that a shopper can walk one aisle at a
# time and only look at that section's items. Ordered roughly by a typical store
# layout. Each entry is (section name, examples shown to the model). This is the
# single source of truth; the frontend mirrors the names in SECTION_ORDER.
SECTIONS: list[tuple[str, str]] = [
    ("warzywa/owoce", "warzywa, owoce, zioła, grzyby, sałata, czosnek, cebula, ziemniaki, pomidory, cytryny"),
    ("nabiał i jaja", "mleko, ser, jogurt, masło, śmietana/śmietanka, twaróg, jaja"),
    ("mięso/ryby/wędliny", "mięso, drób, ryby, owoce morza, kiełbasa, szynka, parówki, wędliny"),
    ("pieczywo", "chleb, bułki, bagietki, tortille, drożdżówki"),
    ("mrożonki", "mrożone warzywa/owoce, mrożona pizza, lody, mrożone frytki, pierogi mrożone"),
    ("produkty suche/sypkie", "makaron, ryż, kasza, mąka, cukier, sól, przyprawy (pieprz, papryka), "
     "olej, oliwa, konserwy, płatki, bakalie"),
    ("napoje", "woda, soki, napoje gazowane, kawa, herbata, piwo, wino"),
    ("słodycze/przekąski", "czekolada, ciastka, cukierki, chipsy, orzeszki, batony"),
    ("chemia/dom", "papier toaletowy, ręczniki papierowe, płyn do naczyń, proszek do prania, "
     "worki na śmieci, folia, baterie, żarówki"),
    ("higiena/kosmetyki", "mydło, szampon, pasta do zębów, dezodorant, podpaski, pielucha, krem"),
    ("inne", "cokolwiek, co nie pasuje do powyższych sekcji"),
]

SECTIONS_ORDER = [name for name, _ in SECTIONS]


def _sections_block() -> str:
    """Render the aisle taxonomy with examples for the prompt (one source of truth)."""
    lines = []
    width = max(len(name) for name, _ in SECTIONS)
    for name, examples in SECTIONS:
        lines.append(f"   - {name.ljust(width)}  ({examples})")
    return "\n".join(lines)


def shopping_list_instructions(config: TenantConfig) -> str:
    return f"""You are a shopping list organiser. Your job is to process a raw list of
shopping items (from recipes and/or typed by the user) and return a structured
shopping list. Items may be anything a person buys — NOT only cooking ingredients
(e.g. "baterie AA", "worki na śmieci", "znaczki"). Keep every input item.

The sections mirror supermarket aisles: the shopper wants to look at ONE section at
a time and find everything for that aisle there — so assign the aisle where the item
is actually sold (garlic/onion → warzywa/owoce, toilet paper → chemia/dom).

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
4. Assign each item to EXACTLY ONE section from this fixed list (use the section
   name verbatim). Match by which supermarket aisle sells the item, using the
   examples as a guide:
{_sections_block()}
   Use "inne" only as a last resort — most household, hygiene and drink items have
   a proper section above (papier toaletowy → chemia/dom, not inne). Garlic, onion,
   potatoes and other vegetables go to warzywa/owoce, never inne.
5. Within each section sort items alphabetically by name.
6. Return only sections that have at least one item.
7. The `sections` field must list only the section names present, in the order
   they appear in the list above.
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
