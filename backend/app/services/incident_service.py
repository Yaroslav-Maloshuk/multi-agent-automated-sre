"""Incident orchestration service: the REST/WS-facing facade over the graph.

Keeps an in-memory registry of incidents and their live agent states so the
command center can render graphs, time-travel and ROI at any moment.

Two execution modes:

* In-process (default): runs are spawned as asyncio background tasks inside
  the API process (`make backend`, tests, `CELERY_ENABLED=false`).
* Celery (production / `docker compose`): runs are dispatched to the worker
  pool via `auto_sre.run_incident`. Live state and ROI then arrive back over
  the event bus — a dedicated listener keeps `_states` fresh from the
  `memory.snapshot` envelopes the worker publishes on every graph step.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from app.bus.redis_bus import EventBus
from app.bus.schemas import Channel, EventEnvelope, EventType, HITLReview
from app.core.config import Settings
from app.core.errors import ConflictError, GraphNotReadyError, NotFoundError
from app.graph.approvals import ApprovalRegistry
from app.graph.executor import GraphExecutor
from app.graph.state import GraphStatus
from app.memory.hybrid import MemoryStore
from app.services.roi import ROILedger

logger = logging.getLogger(__name__)

_TERMINAL = {GraphStatus.COMPLETED.value, GraphStatus.FAILED.value, GraphStatus.CANCELLED.value, GraphStatus.INTERRUPTED.value}


class IncidentService:
    def __init__(
        self,
        executor: GraphExecutor,
        bus: EventBus,
        roi: ROILedger,
        settings: Settings,
        approval_registry: ApprovalRegistry,
        memory: MemoryStore,
    ) -> None:
        self._executor = executor
        self._bus = bus
        self._roi = roi
        self._settings = settings
        self._approvals = approval_registry
        self._memory = memory
        self._incidents: dict[str, dict[str, Any]] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._celery_tasks: dict[str, str] = {}
        self._listener: asyncio.Task[None] | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Subscribe to the bus so worker-run state + ROI stay visible to the API."""
        if self._listener is None:
            self._listener = asyncio.create_task(self._state_loop(), name="incident-state-loop")

    async def stop(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except asyncio.CancelledError:
                pass
            self._listener = None

    async def _state_loop(self) -> None:
        """Replay live graph state from bus envelopes (worker + in-process)."""
        try:
            async for envelope in self._bus.stream():
                self._absorb(envelope)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never kill the state listener
            logger.exception("incident state loop crashed")
            await asyncio.sleep(1)

    def _absorb(self, envelope: EventEnvelope) -> None:
        if envelope.incident_id is None:
            return
        iid = envelope.incident_id
        if envelope.type is EventType.MEMORY_SNAPSHOT:
            state = envelope.payload.get("state")
            if isinstance(state, dict) and "incident_id" in state:
                self._states[iid] = state
        elif envelope.type is EventType.GRAPH_STATUS_CHANGED and iid in self._states:
            status = envelope.payload.get("status")
            if isinstance(status, str):
                self._states[iid] = {**self._states[iid], "status": status}

    # ── Read ────────────────────────────────────────────────────────────

    def list_incidents(self) -> list[dict[str, Any]]:
        return [
            {"incident_id": iid, "title": inc.get("title"), "severity": inc.get("severity"), "status": self._status(iid)}
            for iid, inc in sorted(self._incidents.items(), key=lambda kv: kv[1].get("created_at", ""), reverse=True)
        ]

    def get(self, incident_id: str) -> dict[str, Any]:
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise NotFoundError("incident not found", details={"incident_id": incident_id})
        return {
            "incident": incident,
            "state": self._live_state(incident_id) or {"incident_id": incident_id, "status": GraphStatus.QUEUED.value},
            "roi": self._roi.for_incident(incident_id),
        }

    def _live_state(self, incident_id: str) -> dict[str, Any] | None:
        """Latest snapshot: in-flight (executor) or bus-fed (worker/final)."""
        live = self._executor.latest_state(incident_id)
        if live is not None:
            return live.to_dict()
        return self._states.get(incident_id)

    def _status(self, incident_id: str) -> str:
        state = self._live_state(incident_id)
        if state is not None and state.get("status"):
            return str(state["status"])
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
        if self._is_running(incident_id):
            raise ConflictError("incident is already running", details={"incident_id": incident_id})

        if self._settings.celery_enabled:
            from app.celery_app import run_incident_task

            result = run_incident_task.delay(incident_id, incident)
            self._celery_tasks[incident_id] = result.id
            logger.info("dispatched incident %s to celery worker (task %s)", incident_id, result.id)
            return {"incident_id": incident_id, "run_id": "pending", "status": "scheduled", "executor": "celery"}

        task = asyncio.create_task(self._run_async(incident_id), name=f"incident-{incident_id}")
        self._tasks[incident_id] = task
        return {"incident_id": incident_id, "run_id": "pending", "status": "scheduled", "executor": "in-process"}

    def _is_running(self, incident_id: str) -> bool:
        if incident_id in self._tasks and not self._tasks[incident_id].done():
            return True
        if incident_id in self._celery_tasks:
            return True
        state = self._live_state(incident_id)
        return state is not None and str(state.get("status")) in {"RUNNING", "PENDING_APPROVAL"}

    async def _run_async(self, incident_id: str) -> None:
        """In-process execution path (tests / local dev)."""
        incident = self._incidents[incident_id]
        try:
            state = await self._executor.run(incident, incident_id=incident_id)
            self._states[incident_id] = state.to_dict()
            await self._roi.record(state)
            await self._emit_terminal_snapshot(state)
            await self._emit_roi_updated(state.incident_id)
        except Exception:  # noqa: BLE001
            logger.exception("incident run failed: %s", incident_id)
            stored = self._states.get(incident_id)
            if stored is not None:
                self._states[incident_id] = {**stored, "error": "run task failed", "status": GraphStatus.FAILED.value}
        finally:
            self._tasks.pop(incident_id, None)

    async def _emit_terminal_snapshot(self, state: Any) -> None:
        await self._bus.publish(
            _env(
                EventType.MEMORY_SNAPSHOT,
                Channel.incident(state.incident_id),
                payload={"state": state.to_dict()},
            )
        )

    async def _emit_roi_updated(self, incident_id: str) -> None:
        await self._bus.publish(
            EventEnvelope(
                type=EventType.ROI_UPDATED,
                channel=Channel.TELEMETRY,
                incident_id=incident_id,
                payload={"incident_id": incident_id},
            )
        )

    async def review(self, review: HITLReview) -> None:
        """Route a signed human decision into the graph (any process)."""
        await self._approvals.submit(review)

    async def interrupt(self, incident_id: str) -> dict[str, Any]:
        if self._settings.celery_enabled:
            return await self._interrupt_celery(incident_id)
        state = await self._executor.interrupt(incident_id)
        self._states[incident_id] = state.to_dict()
        return {"incident_id": incident_id, "status": state.status.value}

    async def _interrupt_celery(self, incident_id: str) -> dict[str, Any]:
        """Signal the worker run to stop via the approval registry + revoke."""
        await self._approvals.cancel(incident_id)
        task_id = self._celery_tasks.pop(incident_id, None)
        if task_id:
            try:
                from app.celery_app import celery

                celery.control.revoke(task_id, terminate=False)
            except Exception:  # noqa: BLE001
                logger.exception("celery revoke failed for %s", incident_id)
        state = self._states.get(incident_id)
        if state is None:
            raise GraphNotReadyError("run has no recoverable state", details={"incident_id": incident_id})
        self._states[incident_id] = {**state, "status": GraphStatus.INTERRUPTED.value}
        return {"incident_id": incident_id, "status": GraphStatus.INTERRUPTED.value}

    async def resume(self, incident_id: str) -> dict[str, Any]:
        """Re-launch a run from the last known incident payload."""
        incident = self._incidents.get(incident_id)
        if incident is None:
            raise NotFoundError("incident not found", details={"incident_id": incident_id})
        return await self.run(incident_id)

    # ── Replay / fork ───────────────────────────────────────────────────

    async def list_snapshots(self, incident_id: str) -> list[dict[str, Any]]:
        """Every trajectory snapshot of the latest run, in step order."""
        state = self._live_state(incident_id)
        refs = state.get("memory_snapshots") if state else None
        if not isinstance(refs, list) or not refs:
            return []
        snapshots: list[dict[str, Any]] = []
        for ref in refs:
            try:
                blob = await self._memory.get_snapshot(str(ref))
            except Exception:  # noqa: BLE001 — stale refs are skipped
                continue
            snapshots.append(
                {
                    "ref": ref,
                    "run_id": blob.get("run_id"),
                    "step_index": blob.get("step_index"),
                    "node": blob.get("node"),
                    "summary": blob.get("summary"),
                    "status": blob.get("status"),
                }
            )
        return snapshots

    async def get_snapshot(self, incident_id: str, ref: str) -> dict[str, Any]:
        state = self._live_state(incident_id)
        refs = state.get("memory_snapshots") if state else None
        if not isinstance(refs, list) or ref not in refs:
            raise NotFoundError("snapshot not found for incident", details={"incident_id": incident_id, "ref": ref})
        try:
            return await self._memory.get_snapshot(ref)
        except KeyError as exc:
            raise NotFoundError("snapshot not found", details={"ref": ref}) from exc

    async def fork(
        self,
        incident_id: str,
        *,
        snapshot_ref: str | None = None,
        title: str | None = None,
        auto_run: bool = True,
    ) -> dict[str, Any]:
        """Spawn a child incident seeded from a trajectory snapshot.

        The child re-runs the graph from the parent's incident payload, so a
        human can tweak the fix upstream and re-watch the pipeline.
        """
        state = self._live_state(incident_id)
        if state is None or not isinstance(state.get("incident"), dict):
            raise NotFoundError("incident has no runnable state", details={"incident_id": incident_id})

        parent = state["incident"]
        run_id = state.get("run_id")
        payload = {
            "title": title or f"Fork of {parent.get('title', incident_id)}",
            "severity": str(parent.get("severity", "high")),
            "service": parent.get("service"),
            "error": parent.get("error"),
            "message": parent.get("message"),
            "target_file": parent.get("target_file"),
            "files": dict(parent.get("files", {}) or {}),
            "source": "fork",
            "raw_payload": {
                "parent_incident_id": incident_id,
                "parent_run_id": run_id,
                "snapshot_ref": snapshot_ref,
            },
            "auto_run": auto_run,
        }
        created = await self.create(payload)
        return created


def _env(type_: EventType, channel: str, payload: dict[str, Any]) -> EventEnvelope:
    return EventEnvelope(type=type_, channel=channel, payload=payload)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
