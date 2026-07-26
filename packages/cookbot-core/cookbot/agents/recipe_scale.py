"""Scale a recipe's ingredient quantities to a different serving count.

This is a SEPARATE step from extraction (see web_search.py). Extraction is
verbatim — it captures quantities and the serving count exactly as the source
page states them. Only afterwards, and only when the user's requested servings
differ from the recipe's own serving count, do we scale.

Kept narrow on purpose: the agent returns ONLY the rescaled ingredient lines and
the new serving count. It never touches name, steps, source_url, or image_url —
provenance is sacred (Architecture Rule / CLAUDE.md "Source URL is sacred").
"""
import structlog
from pydantic import BaseModel
from pydantic_ai import Agent

from cookbot.models.recipe import MAX_PLAUSIBLE_SERVINGS, Recipe
from cookbot.models.tenant import TenantConfig

log = structlog.get_logger()

# A scale ratio outside this band means the anchor is not a portion count.
#
# The motivating case: a large Polish recipe site states "Liczba porcji: 2000g" —
# a YIELD WEIGHT reusing the portions label. The extractor reads "the count stated
# on the page" and faithfully returns servings=2000, which is a perfectly plausible
# integer, so every lower-bound guard below passes. Asked for 2 servings the user
# got every quantity multiplied by 2/2000 = 0.001.
#
# The bounds are deliberately loose — real recipes do run from a single portion to
# a 100-serving party batch, and legitimate scaling spans roughly 1→24. Anything
# beyond this is not a serving count we should be dividing by.
_MIN_SCALE_RATIO = 0.02
_MAX_SCALE_RATIO = 50.0


class ScaledIngredients(BaseModel):
    # The SAME ingredient lines as the input, with quantities scaled to the target
    # serving count. Same count and order — only the numbers change.
    ingredients: list[str]


def build_recipe_scale_agent(config: TenantConfig) -> Agent[None, ScaledIngredients]:
    """Agent that rescales ingredient quantities from one serving count to another."""
    return Agent(
        config.model_recipe_scale,
        output_type=ScaledIngredients,
        defer_model_check=True,
        instructions=(
            f"You are {config.persona}.\n"
            f"You MUST respond exclusively in {config.language}.\n"
            + _SCALE_INSTRUCTIONS
        ),
    )


_SCALE_INSTRUCTIONS = """
You rescale a recipe's ingredient list from an original serving count to a target
serving count. You are given the original servings, the target servings, and the
ingredient lines exactly as written.

## Rules
- Multiply every measurable quantity by (target / original). Example: original 2,
  target 4 → double every amount; original 2, target 3 → multiply by 1.5.
- Keep the SAME ingredients in the SAME order. Do NOT add, drop, merge, or reorder
  any line. The output list must have exactly as many items as the input.
- Keep each line's wording and units; change ONLY the number. Round to a sensible
  cooking quantity (e.g. 225 g not 225.0 g; "1 i 1/2 łyżki" is fine). Whole eggs,
  cloves, cans, etc. round to a practical whole or common fraction.
- Ingredients with no quantity (e.g. "sól do smaku", "pieprz") stay unchanged.
- Never translate; keep the original language and phrasing.
- Do NOT recompute anything other than quantities.
"""


def _implausible_anchor(original_servings: int, target_servings: int) -> bool:
    """Is this pair of counts too absurd to divide by?

    Two independent checks, because they catch different corruptions:

    - **Either count above `MAX_PLAUSIBLE_SERVINGS`** catches the yield-weight case
      ("Liczba porcji: 2000g" → 2000) even when the target happens to make the
      ratio look sane.
    - **The ratio outside [_MIN, _MAX]** catches a plausible-looking anchor paired
      with a wild target, and vice versa.

    Fails safe toward NOT scaling: a recipe shown with its source's own quantities
    is always defensible, whereas silently multiplying every amount by 0.001 is a
    card that looks legitimate and is entirely wrong.
    """
    if original_servings > MAX_PLAUSIBLE_SERVINGS or target_servings > MAX_PLAUSIBLE_SERVINGS:
        return True
    ratio = target_servings / original_servings
    return ratio < _MIN_SCALE_RATIO or ratio > _MAX_SCALE_RATIO


def recipe_scale_prompt(
    recipe: Recipe, original_servings: int, target_servings: int
) -> str:
    lines = "\n".join(f"- {i}" for i in recipe.ingredients)
    return (
        f"Original servings: {original_servings}\n"
        f"Target servings: {target_servings}\n"
        f"Ingredients (scale each quantity, keep every line):\n{lines}"
    )


async def scale_recipe_to_servings(
    recipe: Recipe,
    target_servings: int,
    *,
    agent: Agent[None, ScaledIngredients],
    usage: object | None = None,
) -> Recipe:
    """Return a copy of `recipe` scaled to `target_servings`.

    No-ops (returns the recipe unchanged, only stamping `original_servings`) when:
      - the recipe's own serving count is unknown/zero (no anchor to scale from), or
      - the target already matches the recipe's servings, or
      - target_servings is not a positive number, or
      - either count, or the ratio between them, is outside a plausible cooking
        range — see `_implausible_anchor`.

    On success, `servings` becomes `target_servings`, `original_servings` records
    the pre-scale count, and only `ingredients` are rewritten — name, steps,
    source_url and image_url are preserved verbatim.
    """
    original = recipe.original_servings if recipe.original_servings else recipe.servings

    # Nothing to anchor to, nothing to do, or a nonsense target → stamp origin only.
    if original <= 0 or target_servings <= 0 or target_servings == original:
        return recipe.model_copy(update={"original_servings": original or None})

    # The anchor isn't a portion count (a yield weight, a pack size, a typo) →
    # keep the faithful extraction. Correct amounts for the page's OWN servings
    # beat amounts multiplied by a garbage ratio, and the same reasoning already
    # governs the line-count rejection below.
    if _implausible_anchor(original, target_servings):
        log.warning(
            "recipe_scale_skipped_implausible",
            recipe_name=recipe.name,
            source_url=recipe.source_url,
            original_servings=original,
            target_servings=target_servings,
        )
        return recipe.model_copy(update={"original_servings": original})

    result = await agent.run(
        recipe_scale_prompt(recipe, original, target_servings),
        usage=usage,  # type: ignore[arg-type]
    )
    scaled = result.output.ingredients

    # Defensive: a good scale keeps the line count. If the model dropped/added
    # lines, distrust it and keep the faithful extraction rather than a mangled
    # ingredient list — better to show correct amounts for the original servings.
    if len(scaled) != len(recipe.ingredients):
        return recipe.model_copy(update={"original_servings": original})

    return recipe.model_copy(update={
        "ingredients": scaled,
        "servings": target_servings,
        "original_servings": original,
    })
