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

    # LLM
    openai_api_key: str
    openai_model: str = "gpt-4o-mini"

    # Optional
    log_level: str = "INFO"
    max_hitl_rounds: int = 3
    session_ttl_hours: int = 24
    firestore_emulator_host: str = ""  # set in .env for local dev, empty on Cloud Run


@lru_cache
def get_settings() -> Settings:
    return Settings()
