"""Live recipe-options search.

Verifies the all-AI regression stays fixed: a dish that demonstrably exists on
the user's default trusted sites (aniagotuje.pl, kwestiasmaku.com) must yield at
least one real web_search proposal — ideally from one of those domains.
"""
from cookbot.agents.recipe_options import build_recipe_options_agent, recipe_options_prompt
from cookbot.models.recipe import ParsedIngredients, UserIntent

_PREFERRED = ["kwestiasmaku.com", "aniagotuje.pl"]


async def test_makaron_ze_szpinakiem_returns_web_proposal(pl_config) -> None:
    intent = UserIntent(
        dish_type="makaron ze szpinakiem",
        servings=2,
        max_time_minutes=30,
        available_ingredients=["makaron", "szpinak"],
        free_notes="",
    )
    parsed = ParsedIngredients(
        items=["makaron", "szpinak"], must_use=[], dietary_hints=[], missing_staples=[]
    )

    agent = build_recipe_options_agent(pl_config)
    result = await agent.run(
        recipe_options_prompt(
            parsed, intent,
            site_filter="",                # sites_and_internet → open web
            allow_ai_generated=True,
            preferred_sites=_PREFERRED,
        )
    )
    proposals = result.output.proposals

    assert len(proposals) >= 1, "agent returned no proposals at all"

    web = [p for p in proposals if p.source == "web_search"]
    assert web, f"expected at least one web_search proposal, got sources: {[p.source for p in proposals]}"

    # Every web proposal must carry a usable URL (provenance).
    for p in web:
        assert p.source_url and p.source_url.startswith("http"), \
            f"web proposal '{p.name}' has no valid source_url: {p.source_url!r}"

    # At least one web proposal should come from a preferred trusted site.
    from_trusted = [
        p for p in web
        if any(dom in (p.source_url or "") for dom in _PREFERRED)
    ]
    assert from_trusted, (
        "expected a proposal from aniagotuje.pl or kwestiasmaku.com; "
        f"web URLs were: {[p.source_url for p in web]}"
    )
