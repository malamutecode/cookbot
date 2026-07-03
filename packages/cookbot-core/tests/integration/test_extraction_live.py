"""Live extraction of a known-good recipe page.

STEP 39 diagnosis: extraction works on real recipe pages — the earlier failure
was the options agent picking a non-recipe article (ofeminin). This guards that a
genuine recipe URL extracts into a full Recipe with provenance, rather than
silently falling back to AI.
"""
from cookbot.agents.chat import OnboardingState, resolve_recipe
from cookbot.agents.web_search import build_web_fetch_agent, web_fetch_prompt
from cookbot.models.recipe import RecipeSummary

# A real aniagotuje recipe page (verified to extract: 7 ingredients, 6 steps).
_KNOWN_GOOD_URL = "https://aniagotuje.pl/przepis/makaron-ze-szpinakiem"

# The page from the reported bug: extractor dropped "cebula" and inflated the
# makaron amount to 200g when the page states 150g for its own serving count.
_KWESTIASMAKU_URL = "https://www.kwestiasmaku.com/przepis/makaron-ze-szpinakiem"


def _ingredients_text(recipe) -> str:
    return " | ".join(recipe.ingredients).lower()


async def test_known_good_url_extracts_to_web_recipe(pl_config) -> None:
    selected = RecipeSummary(
        name="Makaron ze szpinakiem",
        description="d",
        difficulty="Łatwe",
        total_time_minutes=30,
        key_ingredients=["makaron", "szpinak"],
        source="web_search",
        source_url=_KNOWN_GOOD_URL,
    )

    found = await resolve_recipe(
        selected, "1", OnboardingState(servings=2),
        config=pl_config, site_filter="", allow_ai_generated=True,
    )

    assert found.source == "web_search", (
        f"known-good recipe page should extract as web_search, got {found.source!r} "
        f"(fell_back={found.web_pick_fell_back})"
    )
    assert not found.web_pick_fell_back
    assert found.recipe.ingredients, "no ingredients extracted"
    assert found.recipe.steps, "no steps extracted"
    assert found.recipe.source_url and found.recipe.source_url.startswith("http")


async def test_kwestiasmaku_extraction_is_faithful(pl_config) -> None:
    """Regression for the reported bug: extraction must be FAITHFUL to the page.

    Root cause (diagnosed live): the page's ingredient list ("Składniki / 2 porcje")
    starts ~25.8k chars into ~40k of markdown — heavy nav/menu markup precedes it.
    The old 24k fetch cap truncated the list away, so the model never saw it and
    gpt-4o-mini FABRICATED a recipe (wrong makaron amount, missing cebula, phantom
    nutmeg). Raising _MAX_PAGE_CONTENT to 48k lets the real list into context.

    The page (verified) states, for 2 porcje: 150 g makaronu, 1/2 cebuli, 2 ząbki
    czosnku, 1 łyżka oliwy, 1/2 łyżki masła, 150 g szpinaku, 1/3 szklanki śmietanki,
    1/3 szklanki parmezanu. We assert the two values from the bug report exactly
    (150 g makaron, cebula present) plus the serving count. If kwestiasmaku
    restructures the page, update these expected values — do not weaken them.
    """
    agent = build_web_fetch_agent(pl_config)
    recipe = (await agent.run(web_fetch_prompt(_KWESTIASMAKU_URL))).output

    assert recipe is not None, (
        "page has a real recipe — None means the ingredient list was truncated "
        "away again (check _MAX_PAGE_CONTENT vs the page size)"
    )
    ingr = _ingredients_text(recipe)

    # The dropped ingredient from the bug report.
    assert "cebul" in ingr, (
        f"onion (cebula) missing from extraction: {recipe.ingredients}"
    )

    # The page lists 150 g makaronu. The bug fabricated other amounts (200/250/300 g).
    # Require the real value and reject the known fabrications.
    assert "150 g makaron" in ingr, (
        f"expected the page's '150 g makaronu'; got: {recipe.ingredients}"
    )
    for bad in ("200 g makaron", "250 g makaron", "300 g makaron"):
        assert bad not in ingr, (
            f"fabricated/rescaled makaron amount {bad!r} in: {recipe.ingredients}"
        )

    # The page is for 2 portions; extraction must report that, not a pushed-in target.
    assert recipe.servings == 2, f"expected 2 servings from the page, got {recipe.servings}"

    assert recipe.steps
    assert recipe.source_url and recipe.source_url.startswith("http")


async def test_resolve_recipe_scales_when_servings_differ(pl_config) -> None:
    """End-to-end: verbatim extraction, then scaling to the requested servings.

    Requesting more people than the page serves must scale quantities up while
    preserving provenance and recording original_servings. We assert directional
    behaviour (target servings applied, origin recorded, source_url intact) rather
    than exact gram counts, which depend on the live page and the model's rounding.
    """
    selected = RecipeSummary(
        name="Makaron ze szpinakiem",
        description="d",
        difficulty="Łatwe",
        total_time_minutes=30,
        key_ingredients=["makaron", "szpinak"],
        source="web_search",
        source_url=_KWESTIASMAKU_URL,
    )

    found = await resolve_recipe(
        selected, "1", OnboardingState(servings=8),
        config=pl_config, site_filter="", allow_ai_generated=True,
    )

    assert found.source == "web_search"
    recipe = found.recipe
    # If the page states a serving count and it differs from 8, we scaled to 8 and
    # recorded where we scaled from. (If the page happened to already serve 8, the
    # step is a faithful no-op — still valid, servings stays 8.)
    assert recipe.servings == 8
    if recipe.original_servings and recipe.original_servings != 8:
        assert recipe.original_servings > 0
    assert recipe.source_url and recipe.source_url.startswith("http")
    assert recipe.ingredients, "scaling must not empty the ingredient list"
