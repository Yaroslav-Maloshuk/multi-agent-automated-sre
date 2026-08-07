"""Event bus contracts: envelope schema, channel model and client protocol.

The WebSocket JSON envelope below is the *single* wire contract shared by the
async graph, Celery/Ray workers, Redis Pub/Sub and the browser. gRPC framing
(in `proto/runtime.proto`) maps 1:1 onto these events.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EventType(StrEnum):
    # Lifecycle
    INCIDENT_CREATED = "incident.created"
    INCIDENT_UPDATED = "incident.updated"
    # Graph
    GRAPH_STARTED = "graph.started"
    GRAPH_STEP_STARTED = "graph.step.started"
    GRAPH_STEP_COMPLETED = "graph.step.completed"
    GRAPH_STATUS_CHANGED = "graph.status.changed"
    GRAPH_COMPLETED = "graph.completed"
    GRAPH_FAILED = "graph.failed"
    GRAPH_INTERRUPTED = "graph.interrupted"
    GRAPH_RESUMED = "graph.resumed"
    # Agents
    AGENT_STARTED = "agent.started"
    AGENT_STEP = "agent.step"
    AGENT_LOG = "agent.log"
    AGENT_TOKEN_USAGE = "agent.token_usage"
    # Patch / shadow IDE
    PATCH_GENERATED = "patch.generated"
    PATCH_TYPING = "patch.typing"
    PATCH_DIFF = "patch.diff"
    PATCH_APPLIED = "patch.applied"
    # HITL
    PENDING_APPROVAL = "hitl.pending_approval"
    REVIEW_DECIDED = "hitl.review_decided"
    # Sandbox
    SANDBOX_ACQUIRED = "sandbox.acquired"
    SANDBOX_RELEASED = "sandbox.released"
    SANDBOX_STDOUT = "sandbox.stdout"
    # Memory / telemetry
    MEMORY_SNAPSHOT = "memory.snapshot"
    ROI_UPDATED = "telemetry.roi"


class Channel(str):
    """Logical channel names. Redis keys are namespaced via `EventBus`."""

    @staticmethod
    def incident(incident_id: str) -> str:
        return f"incident:{incident_id}"

    @staticmethod
    def run(run_id: str) -> str:
        return f"run:{run_id}"

    TELEMETRY = "telemetry"
    SYSTEM = "system"
    SANDBOX = "sandbox"


class EventEnvelope(BaseModel):
    """Universal envelope for every message crossing the fabric."""

    version: int = Field(default=1)
    id: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:16]}")
    type: EventType
    channel: str
    correlation_id: str | None = None
    incident_id: str | None = None
    run_id: str | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def parse(cls, raw: bytes | str) -> "EventEnvelope":
        return cls.model_validate_json(raw)


# ── Client → Server control messages ─────────────────────────────────────


class ClientAction(StrEnum):
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    HITL_REVIEW = "hitl.review"
    GRAPH_RESUME = "graph.resume"
    GRAPH_INTERRUPT = "graph.interrupt"
    PING = "ping"


class WSClientMessage(BaseModel):
    version: int = Field(default=1)
    action: ClientAction
    channel: str | None = None
    incident_id: str | None = None
    run_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"


class HITLReview(BaseModel):
    """Signed human decision on a pending patch."""

    incident_id: str
    run_id: str | None = None
    patch_ref: str
    action: ReviewAction
    reason: str | None = None
    # For MODIFY: full replacement file contents keyed by path.
    modified_files: dict[str, str] = Field(default_factory=dict)

    def to_event_payload(self) -> dict[str, Any]:
        return self.model_dump()
