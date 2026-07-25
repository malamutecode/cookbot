from dataclasses import dataclass, field

from cookbot.models.ui_strings import UiStrings, ui_strings_for
from cookbot.models.user import TokenQuota


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
    model_recipe_gen: str = "gpt-4o-mini"
    model_web_search: str = "gpt-4o-mini"
    model_recipe_options: str = "gpt-4o-mini"
    model_shopping_list: str = "gpt-4o-mini"
    # Re-ranks the lexical product-match shortlist for delivery-shop matching.
    # A cheap model is enough — it only picks the best of a handful of candidates.
    model_product_rerank: str = "gpt-4o-mini"
    # Scaling ingredient quantities to a new serving count is mechanical arithmetic
    # on already-extracted text — a cheap model is adequate and keeps cost/TPM low.
    model_recipe_scale: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    # How many recipe cards a proposal turn shows. The zero-LLM fast path can
    # afford more (search results are free); every LLM-written proposal costs
    # tokens against the user's quota, so the reasoning path stays smaller.
    proposal_count: int = 4
    proposal_count_fast: int = 6
    # Below this many usable fast-path results, fall back to the RecipeOptionsAgent
    # rather than show a thin set of cards.
    proposal_min_fast: int = 3
    max_hitl_rounds: int = 3
    feature_nutrition: bool = False
    # Delivery shops (from the delivery-shops package) whose product feeds this
    # tenant may match its shopping list against, by shop id (e.g. "frisco").
    # The client contributes only this config; shop code lives in delivery-shops.
    delivery_shops: list[str] = field(default_factory=list)
    # Whether to LLM-re-rank grocery shortlists that came from a shop's own search
    # backend. Default OFF: shops that rank server-side (Frisco) already return the
    # right product, so re-ranking would spend quota re-deciding a solved question.
    # The feed-fallback path re-ranks regardless — lexical shortlists really are
    # ambiguous. Flip this on per-tenant if a shop's own ranking regresses.
    grocery_llm_rerank: bool = False
    # Per-turn guardrails: one chat turn (the ChatAgent run plus every sub-agent
    # call made from its tools, which share usage via usage=ctx.usage) may not
    # exceed these. Protects against runaway tool loops and TPM blowups.
    usage_request_limit: int = 25
    usage_total_tokens_limit: int = 120_000
    # Per-USER (not per-turn) default token budgets, inherited by a new user
    # record until an admin overrides them. 0 ⇒ unlimited. Metering accumulates
    # each turn's total tokens against these across a day / calendar month.
    default_daily_token_limit: int = 0
    default_monthly_token_limit: int = 0
    # Timezone whose day/month boundaries define the quota reset windows.
    quota_timezone: str = "Europe/Warsaw"
    # uids seeded as admins on first record creation (bootstrap before any admin
    # exists). The client sets this from ADMIN_UIDS env.
    admin_uids: list[str] = field(default_factory=list)

    @property
    def ui(self) -> UiStrings:
        return ui_strings_for(self.language)

    def default_quota(self) -> TokenQuota:
        """The token budget a brand-new user record inherits (0 ⇒ unlimited)."""
        return TokenQuota(
            daily_limit=self.default_daily_token_limit,
            monthly_limit=self.default_monthly_token_limit,
        )
