"""Fast path: zero-LLM DuckDuckGo recipe proposals (STEP 47).

Everything here is hermetic — `DDGS.text` and the page-head fetch are stubbed, so
no network and no model call. The point of the fast path is that a plain dish
request ("znajdź przepis na jagodzianki") produces cards WITHOUT an LLM, so the
strongest assertion in this file is a negative one: no Agent is ever built.
"""
from unittest.mock import patch

from cookbot.agents.chat import OnboardingState
from cookbot.agents.recipe_search_fast import (
    _BLOCKED_URL_RE,
    PageMeta,
    build_fast_proposals,
    enrich_from_page_head,
    fast_path_query,
    is_fast_path_request,
    rank_recipe_results,
)


def _r(href: str, title: str = "T", body: str = "B") -> dict[str, str]:
    return {"href": href, "title": title, "body": body}


# ── URL ranking / filtering (pure, no I/O) ────────────────────────────────────

def test_recipe_path_urls_rank_first() -> None:
    results = [
        _r("https://blog.test/2024/o-jagodach"),
        _r("https://aniagotuje.pl/przepis/jagodzianki"),
        _r("https://kuchnia.test/recipe/blueberry-buns"),
    ]
    ranked = rank_recipe_results(results, limit=6)
    hrefs = [r["href"] for r in ranked]
    # /przepis/ and /recipe/ pages outrank a generic blog post.
    assert hrefs[0] == "https://aniagotuje.pl/przepis/jagodzianki"
    assert hrefs[1] == "https://kuchnia.test/recipe/blueberry-buns"


def test_blocked_url_shapes_are_dropped() -> None:
    blocked = [
        _r("https://forum.test/watek/123"),
        _r("https://site.test/tag/ciasta"),
        _r("https://site.test/kategoria/desery"),
        _r("https://site.test/search?q=jagodzianki"),
        _r("https://www.ofeminin.pl/styl-zycia/top-10-ciast"),
        _r("https://site.test/"),  # bare homepage
    ]
    assert rank_recipe_results(blocked, limit=6) == []
    # And each pattern is individually recognised.
    for r in blocked[:-1]:
        assert _BLOCKED_URL_RE.search(r["href"]), r["href"]


def test_domain_deduplication_keeps_best_per_site() -> None:
    results = [
        _r("https://aniagotuje.pl/artykul/o-jagodach"),
        _r("https://aniagotuje.pl/przepis/jagodzianki"),   # better page, same domain
        _r("https://kwestiasmaku.com/przepis/jagodzianki"),
    ]
    ranked = rank_recipe_results(results, limit=6)
    domains = [r["href"].split("/")[2] for r in ranked]
    assert len(domains) == len(set(domains)), "one card per domain"
    # The /przepis/ page is the one kept for aniagotuje.
    assert "https://aniagotuje.pl/przepis/jagodzianki" in [r["href"] for r in ranked]


def test_ranking_respects_limit() -> None:
    results = [_r(f"https://site{i}.test/przepis/x") for i in range(10)]
    assert len(rank_recipe_results(results, limit=6)) == 6


# ── Trigger predicate ─────────────────────────────────────────────────────────

def test_plain_dish_request_takes_fast_path() -> None:
    ob = OnboardingState(dish_type="jagodzianki")
    assert is_fast_path_request(ob, message="znajdź przepis na jagodzianki") is True


def test_vague_request_does_not_take_fast_path() -> None:
    ob = OnboardingState(dish_type="any")
    assert is_fast_path_request(ob, message="zaproponuj coś na obiad") is False


def test_vague_dish_paraphrases_do_not_take_fast_path() -> None:
    """The prompt asks the model to record a vague answer as dish_type="any", but
    it paraphrases in the tenant's language. Observed live: "Obiad" (turn 1 of
    guided onboarding) became dish_type="jakiekolwiek", which fired the fast path
    and searched DDG for "jakiekolwiek przepis" — returning basketball rules and a
    TikTok video as recipe cards, mid-onboarding."""
    for vague in ["jakiekolwiek", "cokolwiek", "Jakiekolwiek", "nie wiem", "dowolne", "wszystko"]:
        ob = OnboardingState(dish_type=vague)
        assert is_fast_path_request(ob, message="Obiad") is False, f"{vague!r} fired the fast path"


def test_meal_slot_alone_is_not_a_searchable_dish() -> None:
    """"obiad"/"kolacja" name a COURSE, not a dish — searching for them yields
    listicles, not a recipe. They belong in guided onboarding."""
    for slot in ["obiad", "kolacja", "śniadanie", "lunch", "deser"]:
        ob = OnboardingState(dish_type=slot)
        assert is_fast_path_request(ob, message=slot) is False, f"{slot!r} fired the fast path"


def test_each_constraint_disables_fast_path() -> None:
    """A constraint appended to a DDG query is a keyword, not an honoured
    requirement — those requests must reach the reasoning agent."""
    base = dict(dish_type="jagodzianki")
    assert is_fast_path_request(OnboardingState(**base, max_time_minutes=30), message="x") is False
    assert is_fast_path_request(OnboardingState(**base, ingredients=["jagody"]), message="x") is False
    assert is_fast_path_request(OnboardingState(**base, free_notes="bez cukru"), message="x") is False


def test_constraint_keyword_in_raw_message_disables_fast_path() -> None:
    """The message is checked even when OnboardingState looks clean — the state is
    filled by the same model call we are trying to make cheap."""
    ob = OnboardingState(dish_type="jagodzianki")
    assert is_fast_path_request(ob, message="przepis na jagodzianki bez cukru") is False
    assert is_fast_path_request(ob, message="szybki przepis na jagodzianki") is False


def test_empty_message_fails_closed() -> None:
    """Without a message the constraint gate cannot be checked, so the shortcut is
    not taken. Fails closed: the cost is one slow turn, never a wrong one."""
    assert is_fast_path_request(OnboardingState(dish_type="jagodzianki"), message="") is False
    assert is_fast_path_request(OnboardingState(dish_type="jagodzianki"), message="   ") is False


def test_servings_alone_does_not_disable_fast_path() -> None:
    """"dla 4 osób" only changes scaling AFTER the pick — it does not change which
    pages are worth showing, so it must not cost the user the fast path."""
    ob = OnboardingState(dish_type="jagodzianki", servings=4)
    assert is_fast_path_request(ob, message="przepis na jagodzianki dla 4 osób") is True


def test_query_uses_dish_and_site_filter() -> None:
    ob = OnboardingState(dish_type="jagodzianki")
    assert fast_path_query(ob, site_filter="") == "jagodzianki przepis"
    assert fast_path_query(ob, site_filter="site:a.com") == "jagodzianki przepis site:a.com"


# ── Proposal construction (zero LLM) ──────────────────────────────────────────

async def test_proposals_are_built_verbatim_with_no_metadata() -> None:
    results = [_r("https://aniagotuje.pl/przepis/jagodzianki", title="Jagodzianki", body="Puszyste drożdżowe")]
    with patch("cookbot.agents.recipe_search_fast._ddg_search", return_value=results), \
         patch("cookbot.agents.recipe_search_fast.enrich_from_page_head", return_value=None):
        props = await build_fast_proposals("jagodzianki przepis", limit=6)

    assert len(props) == 1
    p = props[0]
    assert p.name == "Jagodzianki"
    assert p.description == "Puszyste drożdżowe"
    assert p.source == "web_search"
    assert p.source_url == "https://aniagotuje.pl/przepis/jagodzianki"
    # Zero-LLM cards must NOT invent metadata — the frontend hides empty chips.
    assert p.difficulty == ""
    assert p.total_time_minutes == 0
    assert p.key_ingredients == []


async def test_og_description_beats_ddg_snippet() -> None:
    """og:description is the site author's own one-liner and comes from bytes we
    already fetch for og:image — strictly better copy than an SEO snippet."""
    results = [_r("https://aniagotuje.pl/przepis/x", title="Jagodzianki", body="Sprawdź nasz przepis! Zobacz...")]
    meta = PageMeta(image_url="https://img.test/a.jpg", description="Drożdżowe bułeczki z jagodami.")
    with patch("cookbot.agents.recipe_search_fast._ddg_search", return_value=results), \
         patch("cookbot.agents.recipe_search_fast.enrich_from_page_head", return_value=meta):
        props = await build_fast_proposals("jagodzianki przepis", limit=6)

    assert props[0].description == "Drożdżowe bułeczki z jagodami."
    assert props[0].image_url == "https://img.test/a.jpg"


async def test_jsonld_fills_metadata_when_the_page_has_it() -> None:
    """Where a page ships schema.org/Recipe JSON-LD the chips fill in with REAL
    page data — still zero LLM calls."""
    results = [_r("https://kwestiasmaku.com/przepis/x", title="Jagodzianki")]
    meta = PageMeta(total_time_minutes=90, key_ingredients=["mąka", "jagody", "drożdże"])
    with patch("cookbot.agents.recipe_search_fast._ddg_search", return_value=results), \
         patch("cookbot.agents.recipe_search_fast.enrich_from_page_head", return_value=meta):
        props = await build_fast_proposals("jagodzianki przepis", limit=6)

    assert props[0].total_time_minutes == 90
    assert props[0].key_ingredients == ["mąka", "jagody", "drożdże"]


# ── JSON-LD / og parsing against real page HTML ───────────────────────────────
# These call the PARSER directly rather than stubbing enrich_from_page_head, so
# they exercise the code the stubbed tests above skip past. A `html` shadowing
# bug in _parse_jsonld_recipe survived the stubbed suite and only surfaced on a
# live page that actually had JSON-LD ingredients.

_PAGE_HTML = """<html><head>
<meta property="og:image" content="https://img.test/a.jpg?w=1&amp;h=2">
<meta property="og:description" content="Dro&#380;d&#380;owe bu&#322;eczki.">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Recipe","name":"Jagodzianki",
 "totalTime":"PT1H30M","recipeIngredient":["500 g m&#261;ki","300 g jag&#243;d","50 g dro&#380;d&#380;y"]}
</script></head><body>...</body></html>"""


def test_parse_jsonld_extracts_time_and_ingredients() -> None:
    from cookbot.agents.recipe_search_fast import _parse_jsonld_recipe

    minutes, ingredients = _parse_jsonld_recipe(_PAGE_HTML)
    assert minutes == 90                                  # PT1H30M
    assert ingredients == ["500 g mąki", "300 g jagód", "50 g drożdży"]


async def test_enrich_unescapes_og_image_and_description() -> None:
    """`&amp;` in an og:image query string breaks the <img src>, so unescaping
    happens where the tag is READ — asserted here against real page HTML."""
    import httpx

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_PAGE_HTML, headers={"content-type": "text/html; charset=utf-8"})

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        meta = await enrich_from_page_head(client, "https://site.test/przepis/x")

    assert meta is not None
    assert meta.image_url == "https://img.test/a.jpg?w=1&h=2"
    assert meta.description == "Drożdżowe bułeczki."
    assert meta.total_time_minutes == 90


async def test_enrich_returns_none_on_bot_block() -> None:
    """403 is common (przepisy.pl blocks us) — a lost card's metadata, not an error."""
    import httpx

    transport = httpx.MockTransport(lambda _r: httpx.Response(403, text="denied"))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await enrich_from_page_head(client, "https://site.test/x") is None


def test_parse_jsonld_finds_recipe_inside_graph() -> None:
    """Real pages wrap the Recipe node in @graph or a list."""
    from cookbot.agents.recipe_search_fast import _parse_jsonld_recipe

    graph = ('<script type="application/ld+json">'
             '{"@graph":[{"@type":"WebPage"},{"@type":"Recipe","totalTime":"PT45M",'
             '"recipeIngredient":["mąka"]}]}</script>')
    assert _parse_jsonld_recipe(graph) == (45, ["mąka"])


def test_parse_jsonld_tolerates_malformed_json() -> None:
    from cookbot.agents.recipe_search_fast import _parse_jsonld_recipe

    assert _parse_jsonld_recipe('<script type="application/ld+json">{nope</script>') == (0, [])
    assert _parse_jsonld_recipe("<html>no ld+json here</html>") == (0, [])


def test_iso_duration_parsing() -> None:
    from cookbot.agents.recipe_search_fast import _iso_duration_to_minutes

    assert _iso_duration_to_minutes("PT1H30M") == 90
    assert _iso_duration_to_minutes("PT45M") == 45
    assert _iso_duration_to_minutes("PT2H") == 120
    assert _iso_duration_to_minutes("garbage") == 0


async def test_html_entities_are_decoded_in_card_text() -> None:
    """DDG snippets and og:description arrive entity-encoded. Observed live on
    "żurek": the card read "Ponad 10 najlepszych przepis&#243;w na żurek"."""
    results = [_r("https://site.test/przepis/zurek",
                  title="Żurek &#8211; przepis", body="Ponad 10 przepis&#243;w na &#380;urek")]
    with patch("cookbot.agents.recipe_search_fast._ddg_search", return_value=results), \
         patch("cookbot.agents.recipe_search_fast.enrich_from_page_head", return_value=None):
        props = await build_fast_proposals("żurek przepis", limit=6)

    assert props[0].name == "Żurek – przepis"
    assert props[0].description == "Ponad 10 przepisów na żurek"


async def test_slow_site_cannot_blow_the_latency_budget() -> None:
    """A single stalling host must not hold the turn hostage. Measured live: one
    slow site pushed "żurek" from ~2.9s to 6.2s. Cards ship without metadata."""
    import asyncio

    results = [_r(f"https://site{i}.test/przepis/x") for i in range(3)]

    async def _never_returns(_client, _url):  # noqa: ANN202
        await asyncio.sleep(30)
        raise AssertionError("should have been cancelled by the budget")

    with patch("cookbot.agents.recipe_search_fast._ddg_search", return_value=results), \
         patch("cookbot.agents.recipe_search_fast._ENRICH_TOTAL_BUDGET_SECONDS", 0.05), \
         patch("cookbot.agents.recipe_search_fast.enrich_from_page_head", _never_returns):
        started = asyncio.get_running_loop().time()
        props = await build_fast_proposals("x przepis", limit=6)
        elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 5, f"enrichment budget was not enforced ({elapsed:.2f}s)"
    # The cards still exist and are still usable — just without photos.
    assert len(props) == 3
    assert all(p.source_url and p.name for p in props)
    assert all(p.image_url is None for p in props)


async def test_search_failure_returns_empty_not_raises() -> None:
    """A DDG outage must degrade to the slow path, never crash the turn (Rule 7)."""
    with patch("cookbot.agents.recipe_search_fast._ddg_search", side_effect=RuntimeError("ddg down")):
        assert await build_fast_proposals("jagodzianki przepis", limit=6) == []
