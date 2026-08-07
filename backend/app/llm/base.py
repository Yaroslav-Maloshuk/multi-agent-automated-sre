"""Provider-agnostic LLM interface.

Providers (OpenAI / Anthropic / local HuggingFace) are lazily imported so the
core test suite runs with zero model dependencies. Every provider returns a
typed `LLMResponse` with token accounting for the ROI ledger.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.core.errors import LLMError


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str
    extra: dict = field(default_factory=dict)


class LLMClient(ABC):
    """Async chat-completion client used by every agent."""

    @abstractmethod
    async def complete(self, system: str, user: str) -> LLMResponse:
        raise NotImplementedError

    async def complete_json(self, system: str, user: str) -> tuple[dict, LLMResponse]:
        response = await self.complete(system, user)
        return _extract_json(response.text), response


def estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars / token) used when the provider does not
    report usage — keeps the ROI ledger honest without an extra model call."""
    return max(1, len(text) // 4)


def _extract_json(text: str) -> dict:
    import json
    import re

    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise LLMError("model returned malformed JSON", details={"preview": text[:500]})
