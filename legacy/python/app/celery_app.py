"""Celery application + background incident runner.

The API can dispatch incident resolution to the worker pool; the worker runs
the async graph inside its own asyncio loop. Redis acts as broker + result
backend, the same Redis that powers the event bus.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from celery import Celery

from app.core.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

celery = Celery(
    "auto_sre",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3000,
)


@celery.task(name="auto_sre.run_incident", bind=True)  # type: ignore[untyped-decorator]
def run_incident_task(self, incident_id: str, incident: dict[str, Any]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Resolve an incident through the agent graph, inside the worker."""
    return asyncio.run(_resolve(incident_id, incident))


async def _resolve(incident_id: str, incident: dict[str, Any]) -> dict[str, Any]:
    from app.bus.redis_bus import RedisEventBus
    from app.bus.schemas import Channel, EventType
    from app.graph.approvals import RedisApprovalRegistry
    from app.graph.executor import GraphExecutor
    from app.llm.providers.factory import build_llm
    from app.memory.hybrid import PostgresHybridMemory
    from app.sandbox.pool import build_sandbox_pool
    from app.services.roi import PostgresROILedger

    cfg = get_settings()
    bus = RedisEventBus(cfg)
    await bus.start()
    memory = PostgresHybridMemory(cfg)
    await memory.start()
    roi = PostgresROILedger(cfg)
    await roi.start()
    pool = build_sandbox_pool(cfg)
    await pool.start()
    approvals = RedisApprovalRegistry(cfg)
    await approvals.start()
    llm = None
    try:
        llm = build_llm(cfg)
        executor = GraphExecutor(
            bus=bus, llm=llm, sandbox_pool=pool, memory=memory, settings=cfg, approval_registry=approvals
        )
        state = await executor.run(incident, incident_id=incident_id)
        state_dict = state.to_dict()

        # Publish the terminal snapshot + ROI so the API/UI converge even though
        # the run happened in a different process.
        await bus.publish(
            _env(EventType.MEMORY_SNAPSHOT, Channel.incident(incident_id), incident_id, state.run_id, {"state": state_dict})
        )
        await roi.record(state)
        await bus.publish(_env(EventType.ROI_UPDATED, Channel.TELEMETRY, incident_id, state.run_id, {"incident_id": incident_id}))
        return state_dict
    finally:
        from app.llm.tracing import TracedLLM

        if isinstance(llm, TracedLLM):
            llm.flush()
        await approvals.close()
        await pool.stop()
        await roi.close()
        await memory.close()
        await bus.stop()


def _env(type_: Any, channel: str, incident_id: str, run_id: str, payload: dict[str, Any]) -> Any:
    from app.bus.schemas import EventEnvelope

    return EventEnvelope(type=type_, channel=channel, incident_id=incident_id, run_id=run_id, payload=payload)
