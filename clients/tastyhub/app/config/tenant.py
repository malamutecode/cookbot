from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from cookbot.models.tenant import TenantConfig

_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class _TastyHubSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    tenant_id: str = "tastyhub"
    openai_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    max_hitl_rounds: int = 3
    recipe_source_url: str = "https://tastyhub.com/sitemap-recipes.xml"
    allowed_origins: list[str] = ["*"]


_settings = _TastyHubSettings()

TASTYHUB_CONFIG = TenantConfig(
    tenant_id=_settings.tenant_id,
    persona=(
        "Jesteś przyjaznym i kompetentnym asystentem kulinarnym w TastyHub. "
        "Pomagasz użytkownikom znajdować lub tworzyć pyszne przepisy na podstawie składników, które mają."
    ),
    language="pl",
    recipe_source_url=_settings.recipe_source_url,
    allowed_origins=_settings.allowed_origins,
    model=_settings.openai_model,
    embedding_model=_settings.embedding_model,
    max_hitl_rounds=_settings.max_hitl_rounds,
    feature_nutrition=False,
)
