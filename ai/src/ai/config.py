"""Environment configuration for the AI gateway (env-gated, sane defaults)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_", env_file=".env", extra="ignore")

    # Provider routing (LiteLLM). Empty key means the provider is skipped.
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openai_model: str = "gpt-4o"
    anthropic_model: str = "claude-sonnet-4-20250514"
    vllm_url: str = "http://vllm:8001"
    vllm_model: str = "Qwen/Qwen2.5-3B-Instruct"
    ray_url: str = "http://ray:8001"
    candle_url: str = "http://candle:11434"
    candle_model: str = "Qwen/Qwen2.5-3B-Instruct"

    # Optional LiteLLM proxy base (http://litellm:4000). When set, every
    # non-`local` chat/stream call is routed through the proxy instead of
    # reaching the provider directly (proxy model names apply).
    proxy_url: str = ""

    # Retrieval endpoints.
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "sre_snapshots"
    lancedb_path: str = "var/lancedb"
    sqlite_path: str = "var/vectorless.db"

    # Default model used by /v1/chat/completions when none is requested.
    default_model: str = "candle/Qwen/Qwen2.5-3B-Instruct"

    # SSE stream token cadence (seconds) for the deterministic local provider.
    stream_delay: float = 0.05


@lru_cache
def get_settings() -> Settings:
    return Settings()
