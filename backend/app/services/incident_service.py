"""Incident orchestration service: the REST/WS-facing facade over the graph.

Keeps an in-memory registry of incidents and their live agent states so the
command center can render graphs, time-travel and ROI at any moment. Runs are
spawned as background tasks; every transition is already streamed over the bus.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.bus.redis_bus import EventBus
from app.bus.schemas import Channel, EventType, HITLReview
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.graph.executor import GraphExecutor
from app.graph.state import AgentState, GraphStatus
from app.services.roi import ROILedger

logger = logging.getLogger(__name__)


class IncidentService:
    def __init__(self, executor: GraphExecutor, bus: EventBus, roi: ROILedger, settings: Settings) -> None:
        self._executor = executor
        self._bus = bus
        self._roi = roi
        self._settings = settings
        self._incidents: dict[str, dict[str, Any]] = {}
        self._states: dict[str, AgentState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    # ── Read ────────────────────────────────────────────────────────────

    def list(self) -> list[dict[str, Any]]:
        return [
            {"incident_id": iid, "title": inc.get("title"), "severity": inc.get("severity"), "status": self._status(iid)}
            for iid, inc in sorted(self._incidents.items(), key=lambda kv: kv[1].get("created_at", ""), reverse=True)
        ]

    def get(self, incident_id: str) -> dict[str, Any]:
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise NotFoundError("incident not found", details={"incident_id": incident_id})
        state = self._states.get(incident_id)
        return {
            "incident": incident,
            "state": state.to_dict() if state else {"incident_id": incident_id, "status": "QUEUED"},
            "roi": self._roi.for_incident(incident_id),
        }

    def _status(self, incident_id: str) -> str:
        state = self._states.get(incident_id)
        if state is not None and state.status:
            return state.status.value
        return GraphStatus.QUEUED.value

    # ── Mutations ───────────────────────────────────────────────────────

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        incident_id = f"inc_{uuid.uuid4().hex[:16]}"
        incident = {
            "id": incident_id,
            "created_at": _now_iso(),
            **payload,
        }
        self._incidents[incident_id] = incident
        await self._bus.publish(
            _env(
                EventType.INCIDENT_CREATED,
                Channel.incident(incident_id),
                payload={"incident_id": incident_id, "title": payload.get("title")},
            )
        )
        if payload.get("auto_run"):
            await self.run(incident_id)
        return {"incident_id": incident_id, "incident": incident}

    async def run(self, incident_id: str) -> dict[str, Any]:
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise NotFoundError("incident not found", details={"incident_id": incident_id})
        if incident_id in self._tasks and not self._tasks[incident_id].done():
            raise ConflictError("incident is already running", details={"incident_id": incident_id})

        task = asyncio.create_task(self._run_async(incident_id), name=f"incident-{incident_id}")
        self._tasks[incident_id] = task
        return {"incident_id": incident_id, "run_id": "pending", "status": "scheduled"}

    async def _run_async(self, incident_id: str) -> None:
        incident = self._incidents[incident_id]
        try:
            state = await self._executor.run(incident, incident_id=incident_id)
            self._states[incident_id] = state
            self._roi.record(state)
        except Exception:  # noqa: BLE001
            logger.exception("incident run failed: %s", incident_id)
            state = self._states.get(incident_id)
            if state is not None:
                failed = state.replace(error="run task failed").finish(GraphStatus.FAILED)
                self._states[incident_id] = failed
        finally:
            self._tasks.pop(incident_id, None)

    def review(self, review: HITLReview) -> None:
        """Route a signed human decision into the graph (blocking future)."""
        self._executor.submit_review(review)

    async def interrupt(self, incident_id: str) -> dict[str, Any]:
        state = await self._executor.interrupt(incident_id)
        self._states[incident_id] = state
        return {"incident_id": incident_id, "status": state.status.value}

    async def resume(self, incident_id: str) -> dict[str, Any]:
        """Re-launch a run from the last known incident payload."""
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise NotFoundError("incident not found", details={"incident_id": incident_id})
        return await self.run(incident_id)


def _env(type_: EventType, channel: str, payload: dict[str, Any]):
    from app.bus.schemas import EventEnvelope

    return EventEnvelope(type=type_, channel=channel, payload=payload)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
