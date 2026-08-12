"""Event bus implementations.

`RedisEventBus` is the production transport. `InMemoryEventBus` mirrors its
interface so the entire stack (graph, WS manager, agents) runs under pytest
with zero external services.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from app.bus.schemas import EventEnvelope
from app.core.config import Settings

logger = logging.getLogger(__name__)


class EventBus(ABC):
    """Asynchronous pub/sub fabric for graph telemetry."""

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def publish(self, envelope: EventEnvelope) -> None:
        raise NotImplementedError

    @abstractmethod
    async def healthy(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def stream(self) -> AsyncIterator[EventEnvelope]:
        """Consume every event published on the fabric (namespaced fan-out)."""


class InMemoryEventBus(EventBus):
    """Thread-/asyncio-safe in-process bus used by tests and local dev."""

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[EventEnvelope]] = set()
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        for queue in list(self._queues):
            queue.put_nowait(EventEnvelope(type="system", channel="system", payload={"shutdown": True}))

    async def publish(self, envelope: EventEnvelope) -> None:
        for queue in list(self._queues):
            queue.put_nowait(envelope)

    async def healthy(self) -> bool:
        return self._running

    def stream(self) -> AsyncIterator[EventEnvelope]:
        return self._make_stream()

    async def _make_stream(self) -> AsyncIterator[EventEnvelope]:
        queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=10_000)
        self._queues.add(queue)
        try:
            while self._running:
                envelope = await queue.get()
                yield envelope
                if envelope.type.value == "system" and envelope.payload.get("shutdown"):
                    break
        finally:
            self._queues.discard(queue)


class RedisEventBus(EventBus):
    """Production bus: Redis Pub/Sub with a namespaced wildcard consumer."""

    def __init__(self, settings: Settings) -> None:
        self._namespace = settings.event_namespace
        self._url = settings.redis_url
        self._pubsub: Any = None
        self._queue: asyncio.Queue[EventEnvelope] = asyncio.Queue(maxsize=10_000)
        self._task: asyncio.Task[None] | None = None
        self._redis: Any = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(self._url, decode_responses=True)  # type: ignore[no-untyped-call]
        await self._redis.ping()
        self._pubsub = self._redis.pubsub()
        await self._pubsub.psubscribe(f"{self._namespace}:*")
        self._task = asyncio.create_task(self._drain(), name="redis-bus-drain")
        self._started = True
        logger.info("redis event bus connected to %s", self._url)

    async def _drain(self) -> None:
        assert self._pubsub is not None
        async for message in self._pubsub.listen():
            if message.get("type") == "pmessage":
                try:
                    envelope = EventEnvelope.parse(message["data"])
                    await self._queue.put(envelope)
                except Exception:  # noqa: BLE001 — never kill the drain loop
                    logger.exception("failed to parse bus message")

    async def stop(self) -> None:
        self._started = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._pubsub is not None:
            await self._pubsub.aclose()
        if self._redis is not None:
            await self._redis.aclose()

    async def publish(self, envelope: EventEnvelope) -> None:
        assert self._redis is not None, "bus not started"
        channel = f"{self._namespace}:{envelope.channel}"
        await self._redis.publish(channel, envelope.model_dump_json())

    async def healthy(self) -> bool:
        if self._redis is None:
            return False
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            return False

    def stream(self) -> AsyncIterator[EventEnvelope]:
        return self._consume()

    async def _consume(self) -> AsyncIterator[EventEnvelope]:
        while True:
            yield await self._queue.get()


def build_event_bus(settings: Settings) -> EventBus:
    if settings.is_test or settings.app_env.value == "test":
        return InMemoryEventBus()
    return RedisEventBus(settings)
