"""Live extraction of a known-good recipe page.

STEP 39 diagnosis: extraction works on real recipe pages — the earlier failure
was the options agent picking a non-recipe article (ofeminin). This guards that a
genuine recipe URL extracts into a full Recipe with provenance, rather than
silently falling back to AI.
"""
from cookbot.agents.chat import resolve_recipe, OnboardingState
from cookbot.models.recipe import RecipeSummary

# A real aniagotuje recipe page (verified to extract: 7 ingredients, 6 steps).
_KNOWN_GOOD_URL = "https://aniagotuje.pl/przepis/makaron-ze-szpinakiem"


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
