from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Resolve .env relative to this file: app/config/settings.py → clients/tastyhub/.env
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"  # clients/tastyhub/.env


def _split_csv(v: object) -> object:
    """Accept a comma-separated string for list[str] env vars.

    pydantic-settings otherwise tries to JSON-parse a list field's env value, so
    `ALLOWED_ORIGINS=https://a,https://b` would crash the app at startup. Split
    plain strings on commas; pass through anything already a list (or JSON).
    """
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        if s.startswith("["):  # a JSON array — parse it ourselves (NoDecode is set)
            import json

            return json.loads(s)
        return [part.strip() for part in s.split(",") if part.strip()]
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # GCP
    google_cloud_project: str
    firestore_database: str = "(default)"

    # Client identity
    tenant_id: str = "tastyhub"
    api_key: str
    recipe_source_url: str = "https://tastyhub.com/sitemap-recipes.xml"
    # NoDecode: skip pydantic-settings' eager JSON decode so _split_csv can accept
    # a comma-separated env string (Cloud Run passes plain strings, not JSON).
    allowed_origins: Annotated[list[str], NoDecode] = ["*"]

    # LLM — per-agent model overrides.
    # NOTE on models: gpt-4o-mini is used for the recipe agents because it
    # extracts reliably in one shot. gpt-4o (no -mini) tripped this org's 30k TPM
    # limit on large recipe pages → empty extraction, so we keep the recipe agents
    # on gpt-4o-mini. gpt-4o-mini is not deprecated; swap the model on merit (a
    # better/cheaper option), not on a schedule.
    openai_api_key: str
    # Fast conversational agents
    model_chat: str = "gpt-4o-mini"
    model_shopping_list: str = "gpt-4o-mini"
    # Heavier recipe agents (search/extraction, generation, options)
    model_recipe_gen: str = "gpt-4o-mini"
    model_web_search: str = "gpt-4o-mini"
    model_recipe_options: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"

    # Recipe proposal counts. The zero-LLM fast path (STEP 47) can afford more
    # cards because search results are free; every LLM-written proposal costs
    # tokens against the user's quota. Below proposal_min_fast usable results the
    # fast path defers to the reasoning agent.
    proposal_count: int = 4
    proposal_count_fast: int = 6
    proposal_min_fast: int = 3

    # Optional
    log_level: str = "INFO"
    max_hitl_rounds: int = 3
    session_ttl_hours: int = 24
    firestore_emulator_host: str = ""  # set in .env for local dev, empty on Cloud Run
    dev_uid: str = ""  # when set, x-dev-uid header is accepted as a user identity bypass

    # User management + per-user token quotas (STEP 42)
    admin_uids: Annotated[list[str], NoDecode] = []   # uids seeded as admins (bootstrap)
    default_daily_token_limit: int = 0       # 0 ⇒ unlimited
    default_monthly_token_limit: int = 0     # 0 ⇒ unlimited
    quota_timezone: str = "Europe/Warsaw"    # day/month boundaries for resets

    # Access whitelist: only these may authenticate. Entries are either an exact
    # email ("a@x.com") or a whole domain ("@example.com"). EMPTY = allow anyone
    # who has a valid Firebase token (open sign-in). This is the real access gate
    # (CORS only stops other browsers, not scripts) — see DEPLOY.md.
    allowed_emails: Annotated[list[str], NoDecode] = []

    # Accept comma-separated env strings for list fields (Cloud Run --set-env-vars
    # passes plain strings, not JSON). See _split_csv.
    _split_origins = field_validator("allowed_origins", mode="before")(_split_csv)
    _split_admins = field_validator("admin_uids", mode="before")(_split_csv)
    _split_emails = field_validator("allowed_emails", mode="before")(_split_csv)


@lru_cache
def get_settings() -> Settings:
    return Settings()
