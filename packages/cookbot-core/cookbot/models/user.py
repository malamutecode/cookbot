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
        """Return a DuckDuckGo site: filter string, or empty string if unrestricted."""
        if self.search_mode == "internet_only":
            return ""
        enabled = [s.url for s in self.sources if s.enabled]
        if not enabled:
            return ""
        return " OR ".join(f"site:{url}" for url in enabled)
