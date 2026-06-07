"""recipe_options_prompt query-building.

Regression guards for the all-AI bug: the search query must not wrap a site:
filter in parentheses (unreliable on DDG), and preferred sites must surface as a
soft hint (not a hard filter) in 'sites_and_internet' mode.
"""
from cookbot.agents.recipe_options import recipe_options_prompt
from cookbot.models.recipe import ParsedIngredients, UserIntent

_INTENT = UserIntent(
    dish_type="makaron", servings=2, max_time_minutes=30,
    available_ingredients=["pomidory"], free_notes="",
)
_INGR = ParsedIngredients(items=["pomidory"], must_use=[], dietary_hints=[], missing_staples=[])


def test_prompt_open_web_has_plain_query() -> None:
    p = recipe_options_prompt(_INGR, _INTENT)
    assert 'Search query to use verbatim: "makaron pomidory"' in p
    assert "site:" not in p


def test_prompt_hard_filter_is_not_parenthesised() -> None:
    p = recipe_options_prompt(_INGR, _INTENT, site_filter="site:a.com OR site:b.com")
    # The fragile "(site:a OR site:b) query" form must NOT appear.
    assert "(site:" not in p
    assert "makaron pomidory site:a.com OR site:b.com" in p


def test_prompt_preferred_sites_are_soft_hint() -> None:
    p = recipe_options_prompt(_INGR, _INTENT, preferred_sites=["kwestiasmaku.com", "aniagotuje.pl"])
    assert "Preferred sites" in p
    assert "kwestiasmaku.com" in p
    # Soft preference does not restrict the query itself.
    assert 'Search query to use verbatim: "makaron pomidory"' in p


def test_prompt_ai_allowed_is_topup_only() -> None:
    p = recipe_options_prompt(_INGR, _INTENT, allow_ai_generated=True)
    assert "top up" in p.lower()


def test_prompt_ai_disallowed_says_web_only() -> None:
    p = recipe_options_prompt(_INGR, _INTENT, allow_ai_generated=False)
    assert "NOT ALLOWED" in p
