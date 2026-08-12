"""OpenAI chat + embeddings provider (lazy-imported)."""
from __future__ import annotations

from app.core.config import Settings
from app.core.errors import LLMError
from app.llm.base import LLMClient, LLMResponse, estimate_tokens


class OpenAIProvider(LLMClient):
    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise LLMError("OPENAI_API_KEY is not configured")
        self._settings = settings

    async def complete(self, system: str, user: str) -> LLMResponse:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._settings.openai_api_key)
        try:
            resp = await client.chat.completions.create(
                model=self._settings.openai_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"openai request failed: {exc}") from exc

        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            text=choice.message.content or "",
            input_tokens=usage.prompt_tokens if usage else estimate_tokens(system + user),
            output_tokens=usage.completion_tokens if usage else estimate_tokens(choice.message.content or ""),
        )
