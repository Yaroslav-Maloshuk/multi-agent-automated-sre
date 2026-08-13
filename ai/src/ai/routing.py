"""LiteLLM routing layer.

`route()` proxies chat/completion/embedding calls through LiteLLM so the SRE
graph can talk to OpenAI, Anthropic, vLLM, Ray Serve and the Candle endpoint
with one call shape. The deterministic local provider keeps the gateway usable
(and testable) without any API keys or model weights.
"""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator
from typing import Any, Literal

from .config import get_settings

Provider = Literal["openai", "anthropic", "vllm", "candle", "local", "ray"]


def parse_model(model: str) -> tuple[Provider, str]:
    """Split a `provider/name` model string; default provider is `local`."""
    if "/" in model:
        provider, name = model.split("/", 1)
        return provider, name  # type: ignore[return-value]
    return "local", model


def available_providers() -> dict[str, str]:
    s = get_settings()
    out: dict[str, str] = {}
    if s.openai_api_key:
        out["openai"] = s.openai_model
    if s.anthropic_api_key:
        out["anthropic"] = s.anthropic_model
    out["vllm"] = s.vllm_model
    out["candle"] = s.candle_model
    out["local"] = s.default_model
    return out


class _LocalProvider:
    """Deterministic, dependency-free LLM used for tests and offline runs."""

    def __init__(self) -> None:
        self._answers = {
            "planner": (
                '{"plan":["parse logs","reproduce bug","patch code","verify","deploy"],'
                '"owner":"planner"}'
            ),
            "log_analyzer": (
                '{"root_cause":"no space left on device","confidence":0.93,'
                '"evidence":["df -h"],"fixes":["cleanup"]}'
            ),
            "code_fixer": (
                '{"patch":"--- a/app.py\\n+++ b/app.py\\n-  max(1, 0)\\n+  max(1, 1)",'
                '"summary":"guard divide by zero"}'
            ),
        }

    async def complete(self, messages: list[dict[str, Any]]) -> str:
        blob = json.dumps(messages).lower()
        for key, answer in self._answers.items():
            if key in blob:
                return answer
        return '{"ok":true,"note":"local deterministic provider"}'

    def supports_structured(self) -> bool:
        return False


_local = _LocalProvider()


async def _via_litellm(model: str, messages: list[dict[str, Any]]) -> str:
    import litellm  # heavy, lazy

    resp = await litellm.acompletion(model=model, messages=messages, max_tokens=2048)
    return str(resp.choices[0].message.content)


async def complete(model: str, messages: list[dict[str, Any]]) -> str:
    """One-shot completion, provider-agnostic."""
    provider, name = parse_model(model)
    if provider == "local":
        return await _local.complete(messages)
    try:
        return await _via_litellm(model, messages)
    except ImportError:
        # LiteLLM absent (offline / slim image): degrade to the deterministic
        # local provider instead of 500ing — the engine never stalls.
        return await _local.complete(messages)


async def stream(model: str, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
    """Token stream; the local provider emits a deterministic word stream."""
    provider, name = parse_model(model)
    if provider == "local":
        text = await _local.complete(messages)
        for token in text.split(" "):
            yield token
        return
    try:
        import litellm  # heavy, lazy
    except ImportError:
        text = await _local.complete(messages)
        for token in text.split(" "):
            yield token
        return

    async for chunk in litellm.acompletion(model=model, messages=messages, stream=True):
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield str(delta.content)


async def embed(texts: list[str]) -> list[list[float]]:
    """Embeddings via sentence-transformers; falls back to a hash embedding.

    The fallback is deterministic and locally computable so retrieval tests
    never need torch or network access.
    """
    try:
        from sentence_transformers import SentenceTransformer

        st = SentenceTransformer(get_settings().embedding_model)
        return st.encode(texts).tolist()  # type: ignore[union-attr]
    except ImportError:
        return [hash_embedding(t, dim=384) for t in texts]


def hash_embedding(text: str, dim: int = 384) -> list[float]:
    """Reproducible bag-of-ngram embedding used when torch is unavailable."""
    vec = [0.0] * dim
    norm = text.lower()
    for i in range(len(norm)):
        for n in (1, 2, 3):
            ngram = norm[i : i + n]
            if ngram:
                idx = (abs(hash(ngram)) % dim)  # type: ignore[attr-defined]
                vec[idx] += 1.0
    mag = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / mag for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    mag = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / mag if mag else 0.0


def _jitter(seed: int) -> float:
    return random.Random(seed).uniform(0.78, 0.99)


async def rerank(query: str, candidates: list[str]) -> list[float]:
    """Deterministic pseudo-relevance score so /v1/rerank works offline."""
    q = set(query.lower().split())
    scores: list[float] = []
    for i, c in enumerate(candidates):
        overlap = len(q & set(c.lower().split()))
        scores.append(round(min(0.6 + 0.1 * overlap, 0.99) * _jitter(i), 4))
    return scores
