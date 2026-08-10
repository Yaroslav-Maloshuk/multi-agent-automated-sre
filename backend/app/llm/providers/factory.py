"""LLM client factory."""
from __future__ import annotations

from app.core.config import LLMProviderName, Settings
from app.llm.base import LLMClient
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.local import LocalProvider
from app.llm.providers.openai import OpenAIProvider
from app.llm.tracing import maybe_trace


def build_llm(settings: Settings) -> LLMClient:
    provider = settings.llm_provider
    if provider is LLMProviderName.OPENAI:
        llm: LLMClient = OpenAIProvider(settings)
    elif provider is LLMProviderName.ANTHROPIC:
        llm = AnthropicProvider(settings)
    elif provider is LLMProviderName.LOCAL:
        llm = LocalProvider(settings)
    else:
        raise ValueError(f"unsupported LLM provider: {provider}")
    return maybe_trace(llm, settings)
