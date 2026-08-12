"""Unit tests for the routing layer (no Docker / weights / keys required)."""

import pytest

from ai.routing import complete, cosine, hash_embedding, parse_model, stream


def test_parse_model_splits_provider_and_name():
    assert parse_model("candle/Qwen/Qwen2.5-3B") == ("candle", "Qwen/Qwen2.5-3B")
    assert parse_model("gpt-4o") == ("local", "gpt-4o")


@pytest.mark.asyncio
async def test_local_complete_returns_valid_json():
    text = await complete("local/test", [{"role": "user", "content": "planner: plan this"}])
    assert text.startswith("{")


@pytest.mark.asyncio
async def test_stream_yields_tokens():
    tokens = [t async for t in stream("local/test", [{"role": "user", "content": "planner: go"}])]
    assert tokens
    assert " ".join(tokens).startswith("{")


def test_hash_embedding_is_deterministic_and_normalized():
    a = hash_embedding("no space left on device")
    b = hash_embedding("no space left on device")
    assert a == b
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6
    assert hash_embedding("disk full") != hash_embedding("cpu saturation")


def test_cosine_similarity():
    a = hash_embedding("disk full disk")
    b = hash_embedding("disk full disk")
    c = hash_embedding("network latency spike")
    assert cosine(a, b) > 0.99
    assert cosine(a, c) < cosine(a, b)
