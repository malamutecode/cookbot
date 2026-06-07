"""Shared fixtures for integration tests.

These tests hit REAL services (OpenAI + DuckDuckGo), so they:
  - are marked `integration` (run with `-m integration`, excluded from the unit run),
  - auto-skip unless OPENAI_API_KEY is set,
  - cost money and are slower / occasionally flaky (live web search).
"""
import os
from pathlib import Path

import pytest

from cookbot.models.tenant import TenantConfig

# Every test in this package is an integration test.
pytestmark = pytest.mark.integration


def _load_env_key_if_missing() -> None:
    """Convenience: if OPENAI_API_KEY isn't already exported, read it from the
    tastyhub client's .env so integration tests can `just run` without the
    caller manually exporting it. Never overrides an already-set value.
    """
    if os.getenv("OPENAI_API_KEY"):
        return
    # repo_root/packages/cookbot-core/tests/integration/conftest.py → repo root
    repo_root = Path(__file__).resolve().parents[4]
    env_file = repo_root / "clients" / "tastyhub" / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("OPENAI_API_KEY=") and "=" in line:
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                os.environ["OPENAI_API_KEY"] = value
            break


_load_env_key_if_missing()


def pytest_collection_modifyitems(config, items):
    """Mark all tests under tests/integration/ as integration automatically."""
    for item in items:
        if "tests/integration/" in item.nodeid or "tests\\integration\\" in item.nodeid:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def require_openai() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set — skipping live integration test")


@pytest.fixture
def pl_config(require_openai) -> TenantConfig:
    """A self-contained Polish tenant config (no client app / .env dependency).

    Integration tests default the HEAVY agents to gpt-4o-mini, not gpt-4o. These
    tests verify structure and flow (web proposals exist, recipe has ingredients/
    steps/source_url) — mini is adequate for that and, crucially, has far more TPM
    headroom so multi-turn live runs don't trip OpenAI's per-minute token limit.
    Production keeps gpt-4o; override here with INTEG_MODEL_* env vars if needed.
    """
    return TenantConfig(
        tenant_id="itest",
        persona=(
            "Jesteś przyjaznym i kompetentnym asystentem kulinarnym. "
            "Pomagasz znajdować lub tworzyć przepisy."
        ),
        language="pl",
        recipe_source_url="",
        allowed_origins=[],
        model_chat=os.getenv("INTEG_MODEL_CHAT", "gpt-4o-mini"),
        model_recipe_gen=os.getenv("INTEG_MODEL_RECIPE_GEN", "gpt-4o-mini"),
        model_web_search=os.getenv("INTEG_MODEL_WEB_SEARCH", "gpt-4o-mini"),
        model_recipe_options=os.getenv("INTEG_MODEL_RECIPE_OPTIONS", "gpt-4o-mini"),
        model_shopping_list=os.getenv("INTEG_MODEL_SHOPPING_LIST", "gpt-4o-mini"),
    )
