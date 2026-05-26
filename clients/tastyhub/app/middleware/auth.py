import secrets

from fastapi import Header, HTTPException, status

from app.config.settings import get_settings
from app.config.tenant import TASTYHUB_CONFIG
from cookbot.models.tenant import TenantConfig


async def get_tenant_config(x_api_key: str = Header(...)) -> TenantConfig:
    if not secrets.compare_digest(x_api_key, get_settings().api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return TASTYHUB_CONFIG
