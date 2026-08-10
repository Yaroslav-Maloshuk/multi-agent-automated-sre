"""Optional LLM observability via Langfuse.

Wraps any `LLMClient` with a tracing decorator. When Langfuse credentials are
configured (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`) and the SDK is
installed, every completion is recorded as a generation (input, output, token
usage) against the configured host. Otherwise the wrapper is a transparent
pass-through — telemetry must never break the agent loop.
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings
from app.llm.base import LLMClient, LLMResponse

logger = logging.getLogger(__name__)


class LangfuseTracer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self._settings.langfuse_public_key and self._settings.langfuse_secret_key)

    def _ensure(self) -> Any:
        if self._client is None:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=self._settings.langfuse_public_key,
                secret_key=self._settings.langfuse_secret_key,
                host=self._settings.langfuse_host,
            )
        return self._client

    def generation(
        self,
        *,
        name: str,
        model: str,
        system: str,
        user: str,
        response: LLMResponse,
    ) -> None:
        if not self.enabled:
            return
        try:
            gen = self._ensure().generation(
                name=name,
                model=model,
                input={"system": system, "user": user},
                output=response.text,
                usage={
                    "input": response.input_tokens,
                    "output": response.output_tokens,
                    "unit": "TOKENS",
                },
            )
            gen.end()
        except Exception as exc:  # noqa: BLE001
            logger.debug("langfuse generation failed: %s", exc)

    def flush(self) -> None:
        if self._client is not None:
            try:
                self._client.flush()
            except Exception as exc:  # noqa: BLE001
                logger.debug("langfuse flush failed: %s", exc)


class TracedLLM(LLMClient):
    """LLMClient decorator that records completions into Langfuse."""

    def __init__(self, inner: LLMClient, tracer: LangfuseTracer, model: str) -> None:
        self._inner = inner
        self._tracer = tracer
        self._model = model

    async def complete(self, system: str, user: str) -> LLMResponse:
        response = await self._inner.complete(system, user)
        self._tracer.generation(name="llm.complete", model=self._model, system=system, user=user, response=response)
        return response

    def flush(self) -> None:
        self._tracer.flush()


def maybe_trace(inner: LLMClient, settings: Settings) -> LLMClient:
    """Wrap with tracing only when Langfuse is actually configured."""
    tracer = LangfuseTracer(settings)
    if not tracer.enabled:
        return inner
    model = settings.openai_model if settings.llm_provider.value == "openai" else settings.anthropic_model
    logger.info("llm tracing enabled: host=%s", settings.langfuse_host)
    return TracedLLM(inner, tracer, model)
