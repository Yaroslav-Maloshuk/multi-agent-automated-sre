"""Runtime configuration, loaded from environment via pydantic-settings.

All secrets live in the environment / `.env` — never in source code.
"""
from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LLMProviderName(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"


class Settings(BaseSettings):  # type: ignore[misc]  # pydantic-settings ships no stubs
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────────
    app_name: str = "auto-sre-workspace"
    app_env: AppEnvironment = AppEnvironment.DEVELOPMENT
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: str = "INFO"

    # ── Infrastructure ─────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql://sre:sre@localhost:5432/sre"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ── Event fabric ───────────────────────────────────────────────────
    event_namespace: str = "sre"
    ws_heartbeat_interval: int = 30
    ws_presence_timeout: int = 90

    # ── LLM ────────────────────────────────────────────────────────────
    llm_provider: LLMProviderName = LLMProviderName.LOCAL
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-20250514"
    local_model_path: str = "./models/auto-sre-7b"
    max_tokens_per_run: int = 200_000
    token_price_per_1k_in: float = 0.005
    token_price_per_1k_out: float = 0.015

    # ── Docker sandbox pool ────────────────────────────────────────────
    docker_sandbox_image: str = "auto-sre/sandbox:dev"
    auto_sre_sandbox_image_build_path: str | None = None
    sandbox_pool_min_warm: int = 3
    sandbox_pool_max: int = 5
    sandbox_cpu_quota: float = 1.5
    sandbox_mem_limit: str = "512m"
    sandbox_network_mode: str = "none"
    sandbox_default_workdir: str = "/workspace"
    sandbox_reap_seconds: int = 3600

    # ── Graph orchestrator ─────────────────────────────────────────────
    graph_max_verifier_attempts: int = 3
    graph_hitl_auto_approve: bool = False
    graph_hitl_timeout_seconds: int = 0  # 0 == wait indefinitely
    deploy_webhook_url: str | None = None

    # ── Observability ──────────────────────────────────────────────────
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # Human-in-the-loop: benchmark of average human MITTR, used for ROI.
    human_mttr_seconds: int = 35 * 60  # 35 minutes
    human_loaded_cost_per_hour: float = 150.0

    @property
    def is_test(self) -> bool:
        return self.app_env is AppEnvironment.TEST

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
