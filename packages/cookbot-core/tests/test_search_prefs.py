"""Search-prefs query semantics.

Regression: 'sites_and_internet' (the default) was applying a hard
`site:a OR site:b` DDG filter that frequently returns zero results, so every
recipe proposal fell back to AI. The fix: hard `site:` filter is for 'sites_only'
only; 'sites_and_internet' uses an open-web search with a soft domain preference.
"""
from cookbot.models.user import DEFAULT_SOURCES, RecipeSource, UserSearchPrefs


def _prefs(mode: str) -> UserSearchPrefs:
    return UserSearchPrefs(uid="u", sources=list(DEFAULT_SOURCES), search_mode=mode)


def test_sites_only_uses_hard_site_filter() -> None:
    p = _prefs("sites_only")
    assert p.site_filter() == "site:kwestiasmaku.com OR site:aniagotuje.pl"
    assert p.preferred_sites() == []  # already hard-restricted


def test_sites_and_internet_is_soft_preference() -> None:
    p = _prefs("sites_and_internet")
    # No hard filter → open-web search reliably returns results.
    assert p.site_filter() == ""
    # Domains surface as a soft preference instead.
    assert p.preferred_sites() == ["kwestiasmaku.com", "aniagotuje.pl"]


def test_internet_only_has_no_filter_or_preference() -> None:
    p = _prefs("internet_only")
    assert p.site_filter() == ""
    assert p.preferred_sites() == []


def test_disabled_sources_are_excluded() -> None:
    p = UserSearchPrefs(
        uid="u",
        search_mode="sites_only",
        sources=[
            RecipeSource(url="a.com", name="A", enabled=True),
            RecipeSource(url="b.com", name="B", enabled=False),
        ],
    )
    assert p.site_filter() == "site:a.com"


def test_sites_only_with_no_enabled_sources_is_unrestricted() -> None:
    p = UserSearchPrefs(uid="u", search_mode="sites_only", sources=[])
    assert p.site_filter() == ""
