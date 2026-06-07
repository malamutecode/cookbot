from datetime import datetime

from pydantic import BaseModel


class UserProfile(BaseModel):
    uid: str
    display_name: str
    email: str
    created_at: datetime


class RecipeSource(BaseModel):
    url: str
    name: str         # display name, e.g. "Kwestia Smaku"
    enabled: bool = True


DEFAULT_SOURCES = [
    RecipeSource(url="kwestiasmaku.com", name="Kwestia Smaku"),
    RecipeSource(url="aniagotuje.pl", name="Ania Gotuje"),
]


class UserSearchPrefs(BaseModel):
    uid: str
    sources: list[RecipeSource] = []
    search_mode: str = "sites_and_internet"
    # "sites_only" | "sites_and_internet" | "internet_only"
    allow_ai_generated: bool = True

    def site_filter(self) -> str:
        """Hard DuckDuckGo `site:` restriction — ONLY for 'sites_only' mode.

        Returns "" for 'sites_and_internet' (open web, soft preference applied via
        preferred_sites) and 'internet_only'. A hard `site:a OR site:b` filter is
        unreliable on DDG and can zero out results, so we only use it when the
        user explicitly asked to restrict to their sites.
        """
        if self.search_mode != "sites_only":
            return ""
        enabled = [s.url for s in self.sources if s.enabled]
        if not enabled:
            return ""
        return " OR ".join(f"site:{url}" for url in enabled)

    def preferred_sites(self) -> list[str]:
        """Domains to PREFER (rank up) without excluding the open web.

        Used in 'sites_and_internet' mode as a soft hint in the search prompt.
        Empty for 'sites_only' (already hard-restricted) and 'internet_only'.
        """
        if self.search_mode != "sites_and_internet":
            return []
        return [s.url for s in self.sources if s.enabled]
