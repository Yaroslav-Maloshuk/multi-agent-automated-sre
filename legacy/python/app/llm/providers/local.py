"""Local HuggingFace transformer provider (lazy-imported, optional dep).

Loads any causal-LM from the HuggingFace Hub (or a local directory) and
generates chat completions using the tokenizer's chat template. Requires
`pip install torch transformers accelerate` (see requirements-local.txt).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.config import Settings
from app.core.errors import LLMError
from app.llm.base import LLMClient, LLMResponse


class LocalProvider(LLMClient):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pipe: Callable[..., Any] | None = None
        self._tokenizer: Any = None

    def _ensure_pipeline(self) -> tuple[Callable[..., Any], Any]:
        if self._pipe is not None:
            return self._pipe, self._tokenizer
        try:
            from transformers import AutoTokenizer, pipeline  # type: ignore[import-untyped]
        except ImportError as exc:
            raise LLMError(
                "local provider requires `pip install torch transformers accelerate`"
            ) from exc
        try:
            tokenizer = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call]
                self._settings.local_model_path
            )
            self._tokenizer = tokenizer
            self._pipe = pipeline(
                "text-generation",
                model=self._settings.local_model_path,
                tokenizer=tokenizer,
                device_map="auto",
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"failed to load local model: {exc}") from exc
        return self._pipe, self._tokenizer

    async def complete(self, system: str, user: str) -> LLMResponse:
        import asyncio

        return await asyncio.to_thread(self._generate, system, user)

    def _generate(self, system: str, user: str) -> LLMResponse:
        pipe, tokenizer = self._ensure_pipeline()
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        input_tokens = len(tokenizer(prompt, return_tensors="pt")["input_ids"][0])
        output = pipe(
            prompt,
            max_new_tokens=2048,
            do_sample=False,
            return_full_text=False,
        )
        text = output[0]["generated_text"] if isinstance(output, list) else str(output)
        output_tokens = (
            len(tokenizer(text, return_tensors="pt")["input_ids"][0]) if text else 0
        )
        return LLMResponse(
            text=text.strip(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
