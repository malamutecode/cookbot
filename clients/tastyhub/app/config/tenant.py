from cookbot.models.tenant import TenantConfig

from app.config.settings import get_settings

_s = get_settings()

TASTYHUB_CONFIG = TenantConfig(
    tenant_id=_s.tenant_id,
    persona=(
        "Jesteś przyjaznym i kompetentnym asystentem kulinarnym w TastyHub. "
        "Pomagasz użytkownikom znajdować lub tworzyć pyszne przepisy na podstawie składników, które mają."
    ),
    language="pl",
    recipe_source_url=_s.recipe_source_url,
    allowed_origins=_s.allowed_origins,
    model_chat=_s.model_chat,
    model_recipe_gen=_s.model_recipe_gen,
    model_web_search=_s.model_web_search,
    model_recipe_options=_s.model_recipe_options,
    model_shopping_list=_s.model_shopping_list,
    embedding_model=_s.embedding_model,
    proposal_count=_s.proposal_count,
    proposal_count_fast=_s.proposal_count_fast,
    proposal_min_fast=_s.proposal_min_fast,
    max_hitl_rounds=_s.max_hitl_rounds,
    feature_nutrition=False,
    delivery_shops=["frisco"],
    default_daily_token_limit=_s.default_daily_token_limit,
    default_monthly_token_limit=_s.default_monthly_token_limit,
    quota_timezone=_s.quota_timezone,
    admin_uids=_s.admin_uids,
)
