"""Human-in-the-loop approval registry.

Coordinates the HITL gate across processes:

* In-process runs use `InMemoryApprovalRegistry` (a dict of asyncio futures) —
  the same behaviour the executor has always had.
* Celery-dispatched runs execute inside the worker process while the human
  reviews via the API process. `RedisApprovalRegistry` bridges the two: the
  API stores the signed decision under a Redis key and wakes the waiting worker
  through a per-incident pub/sub channel. Interrupts travel the same channel.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from app.bus.schemas import HITLReview
from app.core.config import Settings


class ApprovalRegistry(ABC):
    @abstractmethod
    async def prepare(self, incident_id: str) -> None:
        """Register a pending gate before awaiting a review."""

    @abstractmethod
    async def wait_for_review(self, incident_id: str, timeout_s: float) -> HITLReview | None:
        """Block until a review arrives; `None` on timeout; raises CancelledError
        on interrupt."""

    @abstractmethod
    async def submit(self, review: HITLReview) -> None:
        """Deliver a signed review to the waiting run (any process)."""

    @abstractmethod
    async def cancel(self, incident_id: str) -> None:
        """Request an interrupt: wake the gate and flag the run for stopping."""

    @abstractmethod
    async def is_interrupted(self, incident_id: str) -> bool:
        """True if an interrupt was requested for this incident."""

    @abstractmethod
    async def clear(self, incident_id: str) -> None:
        """Drop pending approval/interrupt state for a finished run."""


class InMemoryApprovalRegistry(ApprovalRegistry):
    """Process-local registry used by tests and in-process runs."""

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[HITLReview]] = {}
        self._interrupted: set[str] = set()

    async def prepare(self, incident_id: str) -> None:
        if incident_id not in self._futures or self._futures[incident_id].done():
            self._futures[incident_id] = asyncio.get_running_loop().create_future()

    async def wait_for_review(self, incident_id: str, timeout_s: float) -> HITLReview | None:
        if incident_id in self._interrupted:
            raise asyncio.CancelledError
        fut = self._futures.get(incident_id)
        if fut is None or fut.done():
            return None
        try:
            if timeout_s > 0:
                done, _ = await asyncio.wait({fut}, timeout=timeout_s)
                if not done:
                    return None
                return done.pop().result()
            return await asyncio.shield(fut)
        except asyncio.CancelledError:
            raise

    async def submit(self, review: HITLReview) -> None:
        fut = self._futures.get(review.incident_id)
        if fut is not None and not fut.done():
            fut.set_result(review)

    async def cancel(self, incident_id: str) -> None:
        self._interrupted.add(incident_id)
        fut = self._futures.get(incident_id)
        if fut is not None and not fut.done():
            fut.cancel()

    async def is_interrupted(self, incident_id: str) -> bool:
        return incident_id in self._interrupted

    async def clear(self, incident_id: str) -> None:
        self._futures.pop(incident_id, None)
        self._interrupted.discard(incident_id)


class RedisApprovalRegistry(ApprovalRegistry):
    """Cross-process registry: decisions + interrupts flow through Redis.

    Protocol on the per-incident channel `{namespace}:hitl:{id}`:
    `{"kind": "review", "review": {HITLReview dict}}` or `{"kind": "interrupt"}`.
    A submitted review is also persisted under a Redis key so a worker that
    subscribes late still resolves the gate.
    """

    def __init__(self, settings: Settings) -> None:
        self._url = settings.redis_url
        self._namespace = settings.event_namespace
        self._redis: Any = None
        self._started = False

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(self._url, decode_responses=True)  # type: ignore[no-untyped-call]
        await self._redis.ping()
        self._started = True

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        self._started = False

    def _approval_key(self, incident_id: str) -> str:
        return f"{self._namespace}:approval:{incident_id}"

    def _interrupt_key(self, incident_id: str) -> str:
        return f"{self._namespace}:interrupt:{incident_id}"

    def _channel(self, incident_id: str) -> str:
        return f"{self._namespace}:hitl:{incident_id}"

    async def _ensure(self) -> Any:
        await self.start()
        assert self._redis is not None
        return self._redis

    # ── ApprovalRegistry ─────────────────────────────────────────────────

    async def prepare(self, incident_id: str) -> None:
        await self._ensure()

    async def wait_for_review(self, incident_id: str, timeout_s: float) -> HITLReview | None:
        redis = await self._ensure()

        stored = await redis.get(self._approval_key(incident_id))
        if stored:
            return HITLReview.model_validate_json(stored)
        if await self.is_interrupted(incident_id):
            raise asyncio.CancelledError

        pubsub = redis.pubsub()
        await pubsub.subscribe(self._channel(incident_id))
        try:
            if timeout_s > 0:
                return await asyncio.wait_for(self._listen(pubsub), timeout=timeout_s)
            return await self._listen(pubsub)
        finally:
            await pubsub.unsubscribe(self._channel(incident_id))
            await pubsub.aclose()

    async def _listen(self, pubsub: Any) -> HITLReview:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                data = _json_loads(message["data"])
            except (TypeError, ValueError):
                continue
            kind = data.get("kind")
            if kind == "interrupt":
                raise asyncio.CancelledError
            if kind == "review" and isinstance(data.get("review"), dict):
                return HITLReview.model_validate(data["review"])
        raise asyncio.CancelledError  # channel closed before a decision arrived

    async def submit(self, review: HITLReview) -> None:
        redis = await self._ensure()
        ttl = max(60, self._review_ttl())
        payload = _json_dumps({"kind": "review", "review": review.model_dump()})
        await redis.set(self._approval_key(review.incident_id), payload, ex=ttl)
        await redis.publish(self._channel(review.incident_id), payload)

    async def cancel(self, incident_id: str) -> None:
        redis = await self._ensure()
        ttl = max(60, self._review_ttl())
        await redis.set(self._interrupt_key(incident_id), "1", ex=ttl)
        await redis.publish(self._channel(incident_id), _json_dumps({"kind": "interrupt"}))

    async def is_interrupted(self, incident_id: str) -> bool:
        redis = await self._ensure()
        return bool(await redis.get(self._interrupt_key(incident_id)))

    async def clear(self, incident_id: str) -> None:
        redis = await self._ensure()
        await redis.delete(self._approval_key(incident_id), self._interrupt_key(incident_id))

    def _review_ttl(self) -> int:
        # Decisions outlive the HITL timeout so a late submit still resolves.
        return 3600


def build_approval_registry(settings: Settings) -> ApprovalRegistry:
    if settings.is_test:
        return InMemoryApprovalRegistry()
    return RedisApprovalRegistry(settings)


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def _json_loads(value: str) -> Any:
    import json

    return json.loads(value)
