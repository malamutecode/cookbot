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


# --- Prompt shape ---------------------------------------------------------------


def test_scale_prompt_includes_counts_and_all_lines() -> None:
    r = _recipe(2, ["150 g makaronu", "1 cebula"])
    prompt = recipe_scale_prompt(r, original_servings=2, target_servings=5)
    assert "Original servings: 2" in prompt
    assert "Target servings: 5" in prompt
    assert "150 g makaronu" in prompt
    assert "1 cebula" in prompt
