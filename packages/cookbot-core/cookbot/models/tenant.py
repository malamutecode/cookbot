from dataclasses import dataclass, field

from cookbot.models.ui_strings import UiStrings, ui_strings_for


@dataclass
class TenantConfig:
    tenant_id: str
    persona: str
    language: str
    recipe_source_url: str
    allowed_origins: list[str]
    model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    max_hitl_rounds: int = 3
    feature_nutrition: bool = False

    @property
    def ui(self) -> UiStrings:
        return ui_strings_for(self.language)
