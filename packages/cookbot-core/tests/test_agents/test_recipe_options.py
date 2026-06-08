"""recipe_options_prompt query-building.

Regression guards for the all-AI bug: the search query must not wrap a site:
filter in parentheses (unreliable on DDG), and preferred sites must surface as a
soft hint (not a hard filter) in 'sites_and_internet' mode.
"""
from unittest.mock import patch

from cookbot.agents.recipe_options import populate_proposal_images, recipe_options_prompt
from cookbot.models.recipe import ParsedIngredients, RecipeSummary, UserIntent

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


# ── populate_proposal_images (og:image fetch) ─────────────────────────────────

def _summary(name: str, source: str, url: str | None = None, image: str | None = None) -> RecipeSummary:
    return RecipeSummary(
        name=name, description="d", difficulty="Łatwe", total_time_minutes=20,
        key_ingredients=["x"], source=source, source_url=url, image_url=image,
    )


async def test_populate_images_fills_web_proposals_only() -> None:
    web = _summary("A", "web_search", url="https://site.test/a")
    ai = _summary("B", "ai_generated")
    no_url = _summary("C", "web_search", url=None)

    async def _fake_fetch(_client, url):
        return f"{url}/og.jpg"

    with patch("cookbot.agents.recipe_options._fetch_og_image", _fake_fetch):
        await populate_proposal_images([web, ai, no_url])

    assert web.image_url == "https://site.test/a/og.jpg"   # web with URL → filled
    assert ai.image_url is None                             # AI proposal → skipped
    assert no_url.image_url is None                         # web without URL → skipped


async def test_populate_images_tolerates_fetch_failure() -> None:
    web = _summary("A", "web_search", url="https://site.test/a")

    async def _fail(_client, _url):
        return None  # simulate fetch/parse failure

    with patch("cookbot.agents.recipe_options._fetch_og_image", _fail):
        await populate_proposal_images([web])

    assert web.image_url is None  # stays None → frontend shows placeholder


async def test_populate_images_noop_when_no_targets() -> None:
    ai = _summary("B", "ai_generated")
    # No web targets → must not even open an HTTP client (no patch needed, no network).
    await populate_proposal_images([ai])
    assert ai.image_url is None
