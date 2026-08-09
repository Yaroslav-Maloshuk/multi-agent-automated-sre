"""Shared pytest fixtures: zero-external-dependency test environment."""
from __future__ import annotations

import asyncio

import pytest

from app.bus.redis_bus import build_event_bus
from app.core.config import AppEnvironment, Settings
from app.graph.executor import GraphExecutor
from app.llm.base import LLMClient
from app.memory.hybrid import build_memory
from app.sandbox.pool import build_sandbox_pool
from app.services.roi import ROILedger


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env=AppEnvironment.TEST,
        graph_hitl_auto_approve=True,
        graph_max_verifier_attempts=3,
    )


@pytest.fixture
async def bus(settings):
    bus = build_event_bus(settings)
    await bus.start()
    yield bus
    await bus.stop()


@pytest.fixture
async def memory(settings):
    memory = build_memory(settings)
    await memory.start()
    yield memory
    await memory.close()


@pytest.fixture
async def pool(settings):
    pool = build_sandbox_pool(settings)
    await pool.start()
    yield pool
    await pool.stop()


@pytest.fixture
async def events(bus):
    """Collector of every envelope the bus carries, for assertions."""
    seen: list = []
    task = asyncio.create_task(_collect(bus, seen))
    yield seen
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.fixture
def roi(settings):
    return ROILedger(settings)


def build_executor(bus, memory, pool, settings, llm: LLMClient) -> GraphExecutor:
    return GraphExecutor(bus=bus, llm=llm, sandbox_pool=pool, memory=memory, settings=settings)


async def wait_for_event(seen: list, type_, timeout_s: float = 5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        for envelope in seen:
            if envelope.type is type_ or envelope.type.value == type_.value:
                return envelope
        await asyncio.sleep(0.01)
    raise TimeoutError(f"event {type_} not observed")


async def _collect(bus, seen: list) -> None:
    try:
        async for envelope in bus.stream():
            seen.append(envelope)
    except asyncio.CancelledError:
        return
