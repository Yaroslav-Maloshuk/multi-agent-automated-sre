"""Local HuggingFace transformer provider (lazy-imported, optional dep)."""
from __future__ import annotations

from app.core.config import Settings
from app.core.errors import LLMError
from app.llm.base import LLMClient, LLMResponse


class LocalProvider(LLMClient):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pipe: object | None = None

    def _ensure_pipeline(self) -> object:
        if self._pipe is not None:
            return self._pipe
        try:
            from transformers import pipeline  # type: ignore[import-untyped]
        except ImportError as exc:
            raise LLMError("local provider requires `pip install torch transformers`") from exc
        try:
            self._pipe = pipeline("text-generation", model=self._settings.local_model_path, device_map="auto")
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"failed to load local model: {exc}") from exc
        return self._pipe

    async def complete(self, system: str, user: str) -> LLMResponse:
        import asyncio

        prompt = f"{system}\n\n{user}"
        return await asyncio.to_thread(self._generate, prompt)

    def _generate(self, prompt: str) -> LLMResponse:
        pipe = self._ensure_pipeline()
        output = pipe(prompt, max_new_tokens=2048, do_sample=False)
        text = output[0]["generated_text"] if isinstance(output, list) else str(output)
        if text.startswith(prompt):
            text = text[len(prompt) :]
        return LLMResponse(text=text.strip())
