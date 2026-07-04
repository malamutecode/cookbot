from dataclasses import dataclass, field

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
    # Re-ranks the lexical product-match shortlist for delivery-shop matching.
    # A cheap model is enough — it only picks the best of a handful of candidates.
    model_product_rerank: str = "gpt-4o-mini"
    # Scaling ingredient quantities to a new serving count is mechanical arithmetic
    # on already-extracted text — a cheap model is adequate and keeps cost/TPM low.
    model_recipe_scale: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    max_hitl_rounds: int = 3
    feature_nutrition: bool = False
    # Delivery shops (from the delivery-shops package) whose product feeds this
    # tenant may match its shopping list against, by shop id (e.g. "frisco").
    # The client contributes only this config; shop code lives in delivery-shops.
    delivery_shops: list[str] = field(default_factory=list)
    # Per-turn guardrails: one chat turn (the ChatAgent run plus every sub-agent
    # call made from its tools, which share usage via usage=ctx.usage) may not
    # exceed these. Protects against runaway tool loops and TPM blowups.
    usage_request_limit: int = 25
    usage_total_tokens_limit: int = 120_000

    @property
    def ui(self) -> UiStrings:
        return ui_strings_for(self.language)
