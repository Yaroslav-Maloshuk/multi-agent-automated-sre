"""Settings / config parsing."""
from __future__ import annotations

from app.core.config import AppEnvironment, LLMProviderName, Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_env is AppEnvironment.DEVELOPMENT
    assert settings.llm_provider is LLMProviderName.LOCAL
    assert settings.graph_max_verifier_attempts == 3
    assert settings.graph_hitl_auto_approve is False


def test_cors_origins_split() -> None:
    settings = Settings(_env_file=None, cors_origins="http://a.test, http://b.test")
    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


def test_is_test_flag() -> None:
    settings = Settings(_env_file=None, app_env=AppEnvironment.TEST)
    assert settings.is_test is True
