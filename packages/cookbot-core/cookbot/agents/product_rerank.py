"""Product re-rank agent — picks the best delivery-shop product for an ingredient.

The lexical matcher in the ``delivery-shops`` package produces a shortlist of
candidate products but can't tell, say, plain "Masło" from "Masło z czosnkiem" —
both are lexically "butter + one modifier". This agent resolves that ambiguity: it
sees the ingredient and the numbered shortlist and returns the index of the best
match, or ``None`` to decline (keeping the lexical #1).

Kept out of the delivery-shops package on purpose — that package stays
LLM-agnostic and only exposes a ``ReRanker`` callable seam. This agent adapts that
seam to a PydanticAI call driven by ``TenantConfig``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from cookbot.models.tenant import TenantConfig


class ReRankChoice(BaseModel):
    """The re-ranker's decision over a candidate shortlist."""

    choice: int | None = Field(
        default=None,
        description=(
            "1-based index of the best-matching product, or null if none of the "
            "candidates is a good match for the ingredient."
        ),
    )


def build_product_rerank_agent(config: TenantConfig) -> Agent[None, ReRankChoice]:
    return Agent(
        config.model_product_rerank,
        output_type=ReRankChoice,
        defer_model_check=True,
        instructions=f"""You match a cooking ingredient to the best grocery product
from a short candidate list. Language: {config.language}.

You are given an ingredient name and a numbered list of candidate products (each
with its name and shop category). Choose the product a home cook would most likely
want when that ingredient appears on a shopping list.

Rules:
- Prefer the plainest, most basic form of the ingredient. For "masło" (butter),
  prefer plain butter over "masło z czosnkiem" (garlic butter) or flavoured
  variants. For "cukier" prefer plain sugar over vanilla sugar.
- Prefer the correct product TYPE and category over a keyword coincidence. For
  "pierś z kurczaka" (chicken breast) choose a chicken breast/fillet, never liver,
  wings, or sausages that merely mention chicken.
- Respect explicit qualifiers in the ingredient: "śmietana 30%" must be 30%, not
  12% or 18%; "makaron penne" must be penne, not another pasta shape.
- If several candidates are equally good, pick the most generic/standard one.
- If NONE of the candidates is a reasonable match for the ingredient, return null.

Return only the 1-based index of your choice (or null).""",
    )
