"""LLM client factory."""
from __future__ import annotations

from app.core.config import LLMProviderName, Settings
from app.llm.base import LLMClient
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.local import LocalProvider
from app.llm.providers.openai import OpenAIProvider


def build_llm(settings: Settings) -> LLMClient:
    provider = settings.llm_provider
    if provider is LLMProviderName.OPENAI:
        return OpenAIProvider(settings)
    if provider is LLMProviderName.ANTHROPIC:
        return AnthropicProvider(settings)
    if provider is LLMProviderName.LOCAL:
        return LocalProvider(settings)
    raise ValueError(f"unsupported LLM provider: {provider}")
