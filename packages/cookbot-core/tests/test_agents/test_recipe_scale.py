"""Unit tests for the recipe scaling step (verbatim-extraction → scale separation).

These verify the anchor/no-op logic and provenance preservation without hitting a
real LLM — the scaling agent is stubbed with TestModel.
"""
import asyncio

from pydantic_ai.models.test import TestModel

from cookbot.agents.recipe_scale import (
    build_recipe_scale_agent,
    recipe_scale_prompt,
    scale_recipe_to_servings,
)
from cookbot.models.recipe import Recipe
from cookbot.models.tenant import TenantConfig

_CONFIG = TenantConfig(
    tenant_id="test",
    persona="You are a helpful chef",
    language="pl",
    recipe_source_url="",
    allowed_origins=[],
)


def _recipe(
    servings: int, ingredients: list[str], original: int | None = None
) -> Recipe:
    return Recipe(
        name="Makaron ze szpinakiem",
        description="d",
        ingredients=ingredients,
        steps=["Ugotuj makaron.", "Podsmaż szpinak.", "Połącz."],
        prep_time_minutes=10,
        cook_time_minutes=15,
        difficulty="Easy",
        servings=servings,
        tips=[],
        source_url="https://www.kwestiasmaku.com/przepis/makaron-ze-szpinakiem",
        image_url="https://img.example/x.jpg",
        original_servings=original,
    )


def _scale(recipe: Recipe, target: int, scaled_output: list[str] | None) -> Recipe:
    agent = build_recipe_scale_agent(_CONFIG)

    async def _go() -> Recipe:
        model = (
            TestModel(custom_output_args={"ingredients": scaled_output})
            if scaled_output is not None
            else TestModel()
        )
        with agent.override(model=model):
            return await scale_recipe_to_servings(recipe, target, agent=agent)

    return asyncio.run(_go())


# --- No-op paths: the agent must NOT be consulted, quantities stay verbatim -----


def test_no_scale_when_target_equals_servings() -> None:
    # This is the kwestiasmaku case: page serves 2, user asked for 2 → unchanged.
    r = _recipe(2, ["150 g makaronu", "200 g szpinaku", "1 cebula"])
    # scaled_output would break the line count if it were ever applied.
    out = _scale(r, target=2, scaled_output=["WRONG"])
    assert out.ingredients == ["150 g makaronu", "200 g szpinaku", "1 cebula"]
    assert out.servings == 2
    assert out.original_servings == 2  # stamped for transparency


def test_no_scale_when_original_unknown() -> None:
    r = _recipe(0, ["150 g makaronu", "1 cebula"])  # page stated no serving count
    out = _scale(r, target=4, scaled_output=["x", "y"])
    assert out.ingredients == ["150 g makaronu", "1 cebula"]
    assert out.servings == 0


def test_no_scale_when_target_nonpositive() -> None:
    r = _recipe(2, ["150 g makaronu"])
    out = _scale(r, target=0, scaled_output=["x"])
    assert out.ingredients == ["150 g makaronu"]
    assert out.servings == 2


# --- Scaling path ---------------------------------------------------------------


def test_scales_and_records_original_servings() -> None:
    r = _recipe(2, ["150 g makaronu", "200 g szpinaku", "1 cebula"])
    scaled = ["300 g makaronu", "400 g szpinaku", "2 cebule"]
    out = _scale(r, target=4, scaled_output=scaled)
    assert out.ingredients == scaled
    assert out.servings == 4
    assert out.original_servings == 2


def test_scales_up_from_four_to_eight() -> None:
    """The reported case: page serves 4, user asks for 8 (STEP 49).

    The mirror of `test_url_servings_calendar_live.py`'s no-op assertion, at the
    hermetic tier. `original_servings` must record 4 so the calendar entry and the
    UI can say "8 porcji (przeliczone z 4)" — without that anchor the displayed
    count is unverifiable.
    """
    r = _recipe(4, ["4 piersi z kurczaka", "250 ml śmietanki", "1 cebula"])
    scaled = ["8 piersi z kurczaka", "500 ml śmietanki", "2 cebule"]
    out = _scale(r, target=8, scaled_output=scaled)

    assert out.ingredients == scaled
    assert out.servings == 8
    assert out.original_servings == 4
    # Provenance survives the doubling.
    assert out.source_url == "https://www.kwestiasmaku.com/przepis/makaron-ze-szpinakiem"
    assert out.name == "Makaron ze szpinakiem"
    assert out.steps == ["Ugotuj makaron.", "Podsmaż szpinak.", "Połącz."]


def test_scale_up_keeps_line_count() -> None:
    """Doubling must not merge or drop lines — the shopping list reads every one."""
    r = _recipe(4, ["4 piersi z kurczaka", "250 ml śmietanki", "1 cebula", "sól do smaku"])
    scaled = ["8 piersi z kurczaka", "500 ml śmietanki", "2 cebule", "sól do smaku"]
    out = _scale(r, target=8, scaled_output=scaled)
    assert len(out.ingredients) == len(r.ingredients)
    # Unmeasured lines stay verbatim.
    assert out.ingredients[-1] == "sól do smaku"


def test_scaling_preserves_provenance() -> None:
    r = _recipe(2, ["150 g makaronu", "1 cebula"])
    out = _scale(r, target=6, scaled_output=["450 g makaronu", "3 cebule"])
    assert out.source_url == "https://www.kwestiasmaku.com/przepis/makaron-ze-szpinakiem"
    assert out.image_url == "https://img.example/x.jpg"
    assert out.name == "Makaron ze szpinakiem"
    assert out.steps == ["Ugotuj makaron.", "Podsmaż szpinak.", "Połącz."]


def test_uses_original_servings_as_anchor_over_servings() -> None:
    # A recipe already scaled once (servings=4, original=2). Scaling to 2 again
    # anchors on original_servings=2, so target==original → no-op.
    r = _recipe(4, ["300 g makaronu"], original=2)
    out = _scale(r, target=2, scaled_output=["SHOULD-NOT-APPLY"])
    # target (2) == original (2) → no scaling, keep current ingredients.
    assert out.ingredients == ["300 g makaronu"]


def test_line_count_mismatch_is_rejected() -> None:
    # If the model drops or adds an ingredient line, distrust the scale and keep
    # the faithful extraction rather than a mangled list.
    r = _recipe(2, ["150 g makaronu", "200 g szpinaku", "1 cebula"])
    dropped_cebula = ["300 g makaronu", "400 g szpinaku"]  # model lost a line
    out = _scale(r, target=4, scaled_output=dropped_cebula)
    assert out.ingredients == ["150 g makaronu", "200 g szpinaku", "1 cebula"]
    assert out.servings == 2  # unchanged because scale was rejected
    assert out.original_servings == 2


# --- Implausible anchors: a yield weight wearing the "porcje" label -------------


def test_no_scale_when_page_servings_is_a_yield_weight() -> None:
    """The reported bug: a page states "Liczba porcji: 2000g" → servings=2000.

    2000 is a plausible-looking int, so every lower-bound guard passes and the
    scaler multiplied every quantity by 2/2000 = 0.001 — a card that looked
    entirely legitimate (right name, right source, right line count) with
    nonsense amounts. Keep the source's own quantities instead.
    """
    r = _recipe(2000, ["500 g mąki", "250 ml mleka", "2 jajka"])
    out = _scale(r, target=2, scaled_output=["0.5 g mąki", "0.25 ml mleka", "0 jajka"])

    assert out.ingredients == ["500 g mąki", "250 ml mleka", "2 jajka"]
    assert out.servings == 2000  # untouched; the extraction stays faithful
    assert out.original_servings == 2000


def test_no_scale_when_target_is_absurd() -> None:
    """The mirror case — a sane page count with a garbage target."""
    r = _recipe(4, ["4 piersi z kurczaka", "1 cebula"])
    out = _scale(r, target=5000, scaled_output=["WRONG", "WRONG"])
    assert out.ingredients == ["4 piersi z kurczaka", "1 cebula"]
    assert out.servings == 4


def test_no_scale_when_ratio_is_extreme() -> None:
    """Both counts under the cap, but 1 → 90 is not a serving adjustment."""
    r = _recipe(1, ["100 g mąki"])
    out = _scale(r, target=90, scaled_output=["9000 g mąki"])
    assert out.ingredients == ["100 g mąki"]
    assert out.servings == 1


def test_large_but_plausible_batch_still_scales() -> None:
    """The guard must not block real catering-size recipes.

    60 → 30 is a legitimate halving; only the absurd band is rejected.
    """
    r = _recipe(60, ["6 kg mąki", "30 jajek"])
    scaled = ["3 kg mąki", "15 jajek"]
    out = _scale(r, target=30, scaled_output=scaled)
    assert out.ingredients == scaled
    assert out.servings == 30
    assert out.original_servings == 60


def test_boundary_servings_still_scales() -> None:
    """Exactly MAX_PLAUSIBLE_SERVINGS is allowed — the cap is inclusive."""
    r = _recipe(100, ["10 kg mąki"])
    out = _scale(r, target=50, scaled_output=["5 kg mąki"])
    assert out.servings == 50
    assert out.original_servings == 100


def test_implausible_original_servings_anchor_is_rejected() -> None:
    """The bad count can also arrive via `original_servings`, the preferred anchor."""
    r = _recipe(2, ["500 g mąki"], original=2000)
    out = _scale(r, target=4, scaled_output=["WRONG"])
    assert out.ingredients == ["500 g mąki"]


# --- Prompt shape ---------------------------------------------------------------


def test_scale_prompt_includes_counts_and_all_lines() -> None:
    r = _recipe(2, ["150 g makaronu", "1 cebula"])
    prompt = recipe_scale_prompt(r, original_servings=2, target_servings=5)
    assert "Original servings: 2" in prompt
    assert "Target servings: 5" in prompt
    assert "150 g makaronu" in prompt
    assert "1 cebula" in prompt
