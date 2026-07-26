import asyncio

from pydantic_ai.models.test import TestModel

from cookbot.agents.web_search import (
    _EXTRACT_INSTRUCTIONS,
    build_web_search_agent,
    web_fetch_prompt,
    web_search_prompt,
)
from cookbot.models.recipe import ParsedIngredients, Recipe, UserIntent
from cookbot.models.tenant import TenantConfig

_CONFIG = TenantConfig(
    tenant_id="test",
    persona="You are a helpful chef",
    language="en",
    recipe_source_url="",
    allowed_origins=[],
)

_RECIPE_DICT = {
    "name": "Garlic Chicken",
    "description": "Simple garlic chicken.",
    "ingredients": ["200g chicken", "2 cloves garlic"],
    "steps": ["Season chicken.", "Cook in pan.", "Add garlic.", "Serve."],
    "prep_time_minutes": 10,
    "cook_time_minutes": 20,
    "difficulty": "Easy",
    "servings": 2,
    "tips": [],
}

_INTENT = UserIntent(
    dish_type="chicken",
    servings=2,
    max_time_minutes=30,
    available_ingredients=["chicken", "garlic"],
    free_notes="",
)
_INGREDIENTS = ParsedIngredients(items=["chicken", "garlic"], must_use=[], dietary_hints=[], missing_staples=["salt"])


def _run(custom_output: dict | None) -> Recipe | None:
    agent = build_web_search_agent(_CONFIG)

    async def _go() -> Recipe | None:
        # call_tools=[] stops TestModel from auto-invoking the agent's
        # duckduckgo_search / web_fetch tools (which would hit the network /
        # fail on dummy args). These tests only verify output mapping.
        test_model = (
            TestModel(custom_output_args=custom_output, call_tools=[])
            if custom_output
            else TestModel(call_tools=[])
        )
        with agent.override(model=test_model):
            result = await agent.run(web_search_prompt(_INGREDIENTS, _INTENT))
            return result.output

    return asyncio.run(_go())


def test_web_search_returns_recipe_when_found() -> None:
    result = _run(custom_output=_RECIPE_DICT)
    assert isinstance(result, Recipe)
    assert result.name == "Garlic Chicken"


def test_web_search_recipe_has_required_fields() -> None:
    result = _run(custom_output=_RECIPE_DICT)
    assert result is not None
    assert len(result.ingredients) >= 1
    assert len(result.steps) >= 1
    assert result.prep_time_minutes > 0
    assert result.cook_time_minutes > 0
    assert result.difficulty in ("Easy", "Medium", "Hard")


def test_web_search_returns_none_when_no_result() -> None:
    result = _run(custom_output=None)
    # TestModel with no custom output returns default — check it's Recipe or None
    assert result is None or isinstance(result, Recipe)


def test_web_search_prompt_includes_ingredients() -> None:
    prompt = web_search_prompt(_INGREDIENTS, _INTENT)
    assert "chicken" in prompt
    assert "garlic" in prompt


def test_web_search_prompt_includes_time_budget() -> None:
    prompt = web_search_prompt(_INGREDIENTS, _INTENT)
    assert "30" in prompt


def test_web_search_prompt_includes_servings() -> None:
    prompt = web_search_prompt(_INGREDIENTS, _INTENT)
    assert "2" in prompt


def test_web_search_prompt_includes_free_notes() -> None:
    intent_with_notes = UserIntent(
        dish_type="pasta", servings=2, max_time_minutes=0,
        available_ingredients=[], free_notes="easy to reheat"
    )
    prompt = web_search_prompt(_INGREDIENTS, intent_with_notes)
    assert "reheat" in prompt


# --- Verbatim extraction guardrails (regression for kwestiasmaku bug) ---------
# Bug: the fetch agent was told "Adjust servings to N" in the same pass as
# extraction. It rescaled quantities that were already correct (150g makaron → 200g
# for 2 servings) and dropped an ingredient (cebula) while doing the arithmetic.
# Extraction must now be faithful; scaling is a separate concern.


def test_web_fetch_prompt_does_not_instruct_scaling() -> None:
    prompt = web_fetch_prompt("https://example.com/recipe")
    lowered = prompt.lower()
    assert "adjust servings" not in lowered
    assert "scale" not in lowered
    assert "https://example.com/recipe" in prompt


def test_web_fetch_prompt_asks_for_verbatim_extraction() -> None:
    prompt = web_fetch_prompt("https://example.com/recipe").lower()
    # It should signal faithful, as-written extraction (not transformation).
    assert "exactly as written" in prompt


def test_extract_instructions_forbid_scaling() -> None:
    lowered = _EXTRACT_INSTRUCTIONS.lower()
    assert "never scale" in lowered
    # Serving count must come from the page, not be invented/changed.
    assert "stated on the page" in lowered


def test_extract_instructions_flag_commonly_missed_ingredients() -> None:
    # The dropped ingredient was cebula (onion); the prompt now names easy-to-miss
    # staples explicitly so the model double-checks them.
    lowered = _EXTRACT_INSTRUCTIONS.lower()
    assert "onion" in lowered or "cebula" in lowered


# --- Yield-weight servings ("Liczba porcji: 2000g") ------------------------------
# A large Polish recipe site reuses the portions label for the batch weight. The
# extractor faithfully copies "the count stated on the page" → servings=2000, a
# plausible int that then became the scaler's divisor (2/2000 = 0.001).


def test_extract_instructions_reject_servings_with_a_unit() -> None:
    lowered = _EXTRACT_INSTRUCTIONS.lower()
    # The prompt must name the failing shape, not just say "a count".
    assert "2000g" in lowered
    assert "yield" in lowered


def test_yield_weight_servings_is_sanitized_to_unknown() -> None:
    """The output validator maps an implausible count to 0 = "page stated none"."""
    result = _run(custom_output={**_RECIPE_DICT, "servings": 2000})
    assert result is not None
    assert result.servings == 0
    # Only servings is corrected — extraction stays otherwise verbatim.
    assert result.ingredients == ["200g chicken", "2 cloves garlic"]
    assert result.name == "Garlic Chicken"


def test_plausible_servings_passes_through_untouched() -> None:
    result = _run(custom_output={**_RECIPE_DICT, "servings": 6})
    assert result is not None
    assert result.servings == 6


def test_boundary_servings_is_kept() -> None:
    result = _run(custom_output={**_RECIPE_DICT, "servings": 100})
    assert result is not None
    assert result.servings == 100


def test_negative_servings_is_sanitized() -> None:
    result = _run(custom_output={**_RECIPE_DICT, "servings": -3})
    assert result is not None
    assert result.servings == 0


def test_component_block_servings_is_sanitized_too() -> None:
    """A block's count feeds the split heuristic's "differs from main" test."""
    result = _run(custom_output={
        **_RECIPE_DICT,
        "servings": 4,
        "components": [
            {"name": "Curry", "servings": 4, "ingredients": ["kurczak"], "steps": []},
            {"name": "Ciasto", "servings": 2000, "ingredients": ["mąka"], "steps": []},
        ],
    })
    assert result is not None
    assert result.servings == 4
    assert [b.servings for b in result.components] == [4, 0]
