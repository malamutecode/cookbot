from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to this file: app/config/settings.py → clients/tastyhub/.env
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"  # clients/tastyhub/.env


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # GCP
    google_cloud_project: str
    firestore_database: str = "(default)"

    # Client identity
    tenant_id: str = "tastyhub"
    api_key: str
    recipe_source_url: str = "https://tastyhub.com/sitemap-recipes.xml"
    allowed_origins: list[str] = ["*"]

    # LLM — per-agent model overrides.
    # NOTE on models: gpt-4o-mini is used for the recipe agents because it
    # extracts reliably in one shot. gpt-4o (no -mini) tripped this org's 30k TPM
    # limit on large recipe pages → empty extraction. GPT-5 mini was evaluated but
    # gets stuck re-calling the web_fetch tool instead of committing to the
    # structured output (tool-loop), returning None ~3/4 of the time; migrating the
    # recipe agents to GPT-5 needs the Responses API + tool-loop tuning — see
    # TASK.md STEP 40. The 4o family is slated for retirement (June 2026), so that
    # migration is time-bound but not urgent.
    openai_api_key: str
    # Fast conversational agents
    model_chat: str = "gpt-4o-mini"
    model_shopping_list: str = "gpt-4o-mini"
    # Heavier recipe agents (search/extraction, generation, options)
    model_recipe_gen: str = "gpt-4o-mini"
    model_web_search: str = "gpt-4o-mini"
    model_recipe_options: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"

    # Optional
    log_level: str = "INFO"
    max_hitl_rounds: int = 3
    session_ttl_hours: int = 24
    firestore_emulator_host: str = ""  # set in .env for local dev, empty on Cloud Run
    dev_uid: str = ""  # when set, x-dev-uid header is accepted as a user identity bypass


@lru_cache
def get_settings() -> Settings:
    return Settings()
