"""Structured generation tests — pydantic fallback path (no frameworks)."""

import pytest
from pydantic import BaseModel

from ai.structured import _strip_fences, extract


class Plan(BaseModel):
    owner: str
    plan: list[str]


class RootCause(BaseModel):
    root_cause: str
    confidence: float
    evidence: list[str]


def test_strip_fences_removes_markdown():
    assert _strip_fences('```json\n{"a":1}\n```') == '{"a":1}'


@pytest.mark.asyncio
async def test_extract_returns_validated_instance():
    result = await extract(
        "local/test",
        "planner: plan the incident",
        Plan,
    )
    assert isinstance(result, Plan)
    assert result.owner == "planner"
    assert isinstance(result.plan, list)


@pytest.mark.asyncio
async def test_extract_handles_extra_whitespace():
    result = await extract(
        "local/test",
        "log_analyzer: find the root cause",
        RootCause,
    )
    assert isinstance(result, RootCause)
    assert result.confidence > 0


@pytest.mark.asyncio
async def test_extract_rejects_garbage_when_repair_fails():
    from ai.routing import _local

    _local._answers = {}
    with pytest.raises(ValueError):
        await extract("local/test", "say hello", Plan)
    _local._answers = {
        "planner": '{"plan":["parse logs"],"owner":"planner"}',
        "log_analyzer": '{"root_cause":"no space","confidence":0.9,"evidence":["df"]}',
    }
