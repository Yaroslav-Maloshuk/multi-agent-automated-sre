"""Anthropic chat provider (lazy-imported)."""
from __future__ import annotations

from app.core.config import Settings
from app.core.errors import LLMError
from app.llm.base import LLMClient, LLMResponse, estimate_tokens


class AnthropicProvider(LLMClient):
    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not configured")
        self._settings = settings

    async def complete(self, system: str, user: str) -> LLMResponse:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self._settings.anthropic_api_key)
        try:
            resp = await client.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=8192,
                system=system,
                messages=[{"role": "user", "content": user}],
                temperature=0.1,
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"anthropic request failed: {exc}") from exc

        from anthropic.types import TextBlock

        text = "".join(block.text for block in resp.content if isinstance(block, TextBlock))
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=text,
            input_tokens=getattr(usage, "input_tokens", estimate_tokens(system + user)),
            output_tokens=getattr(usage, "output_tokens", estimate_tokens(text)),
        )
