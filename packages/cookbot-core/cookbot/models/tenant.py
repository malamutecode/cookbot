from dataclasses import dataclass

from cookbot.models.ui_strings import UiStrings, ui_strings_for


@dataclass
class TenantConfig:
    tenant_id: str
    persona: str
    language: str
    recipe_source_url: str
    allowed_origins: list[str]
    # Per-agent model overrides — set individually to balance cost vs quality.
    # model_chat / model_shopping_list use a fast cheap model.
    # model_recipe_gen / model_web_search / model_recipe_options default to a
    # stronger model for better recipe quality.
    model_chat: str = "gpt-4o-mini"
    model_recipe_gen: str = "gpt-4o"
    model_web_search: str = "gpt-4o"
    model_recipe_options: str = "gpt-4o"
    model_shopping_list: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    max_hitl_rounds: int = 3
    feature_nutrition: bool = False

    @property
    def ui(self) -> UiStrings:
        return ui_strings_for(self.language)
