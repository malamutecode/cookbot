from datetime import datetime

from pydantic import BaseModel


class UserProfile(BaseModel):
    uid: str
    display_name: str
    email: str
    created_at: datetime


class TokenQuota(BaseModel):
    """Per-user token budget. A limit of 0 means UNLIMITED (no cap) — an admin
    sets a positive number to restrict a user. Metering counts total tokens
    (input + output) summed per chat turn."""

    daily_limit: int = 0    # tokens/day; 0 ⇒ unlimited
    monthly_limit: int = 0  # tokens/month; 0 ⇒ unlimited


class UserRecord(BaseModel):
    """The admin-managed account record for a user, keyed by Firebase uid.
    Distinct from UserProfile (transient identity from the ID token) — this is
    persisted and carries role + quota."""

    uid: str
    email: str | None = None
    display_name: str | None = None
    role: str = "user"          # "user" | "admin"
    quota: TokenQuota = TokenQuota()
    disabled: bool = False
    # Set when an admin creates the account with a generated temp password;
    # cleared once the user picks their own. Firebase has no such concept, so
    # the server owns the flag (STEP 44). Defaulted so pre-STEP-44 Firestore
    # documents still deserialize.
    must_change_password: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class UsageCounter(BaseModel):
    """Accumulated token usage for one reset window. `period_key` identifies the
    window ("2026-07-07" for a day, "2026-07" for a month); when the stored key
    no longer matches the current period the counter is lazily reset to 0."""

    period_key: str
    tokens_used: int = 0


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
