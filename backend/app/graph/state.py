"""State model for the multi-agent orchestration graph.

`AgentState` is an immutable snapshot of an entire incident-resolution run.
Every graph step produces a new state and publishes it on the bus, which is
exactly what powers the time-travel debugger and the ROI ledger.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class GraphStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class AgentName(StrEnum):
    ALERT_INGEST = "alert_ingest"
    PLANNER = "planner"
    LOG_ANALYZER = "log_analyzer"
    CODE_FIXER = "code_fixer"
    HITL_GATE = "hitl_gate"
    VERIFIER = "verifier"
    DEPLOY = "deploy"


class SubtaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Subtask:
    id: str
    title: str
    agent: AgentName
    detail: str = ""
    status: SubtaskStatus = SubtaskStatus.QUEUED


@dataclass(frozen=True, slots=True)
class RootCauseSummary:
    hypothesis: str
    evidence: list[str]
    confidence: float
    memory_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PatchFile:
    path: str
    old_content: str
    new_content: str


class PatchStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"
    APPLIED = "APPLIED"


@dataclass(frozen=True, slots=True)
class Patch:
    patch_ref: str
    files: tuple[PatchFile, ...]
    diff: str
    summary: str
    status: PatchStatus = PatchStatus.DRAFT
    review_reason: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationReport:
    ok: bool
    output: str
    failures: list[str]
    duration_seconds: float
    attempt: int = 1
    coverage: float | None = None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )

    def cost(self, in_price_per_1k: float, out_price_per_1k: float) -> float:
        return (self.input_tokens / 1000) * in_price_per_1k + (self.output_tokens / 1000) * out_price_per_1k


@dataclass(frozen=True, slots=True)
class GraphStepRecord:
    step_index: int
    node: AgentName
    started_at: str
    finished_at: str
    status: GraphStatus
    summary: str
    snapshot_ref: str | None = None
    duration_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class AgentState:
    incident_id: str
    incident: dict[str, Any]
    correlation_id: str
    run_id: str = field(default_factory=lambda: f"run_{uuid.uuid4().hex[:16]}")
    status: GraphStatus = GraphStatus.QUEUED
    created_at: str = field(default_factory=_now_iso)
    subtasks: tuple[Subtask, ...] = ()
    root_cause: RootCauseSummary | None = None
    patch: Patch | None = None
    verification: VerificationReport | None = None
    timeline: tuple[GraphStepRecord, ...] = ()
    memory_snapshots: tuple[str, ...] = ()
    token_usage: TokenUsage = TokenUsage()
    verifier_attempt: int = 0
    verifier_feedback: str | None = None
    hitl_approved: bool = False
    step_index: int = 0
    error: str | None = None
    completed_at: str | None = None

    # ── Transition helpers ──────────────────────────────────────────────

    def replace(self, **changes: Any) -> AgentState:
        return replace(self, **changes)

    def record_step(self, node: AgentName, summary: str, snapshot_ref: str | None = None) -> AgentState:
        started = self.completed_at or self.created_at
        step = GraphStepRecord(
            step_index=self.step_index + 1,
            node=node,
            started_at=started,
            finished_at=_now_iso(),
            status=self.status,
            summary=summary,
            snapshot_ref=snapshot_ref,
        )
        return self.replace(
            step_index=self.step_index + 1,
            timeline=self.timeline + (step,),
        )

    def finish(self, status: GraphStatus) -> AgentState:
        return self.replace(status=status, completed_at=_now_iso())

    def add_tokens(self, usage: TokenUsage) -> AgentState:
        return self.replace(token_usage=self.token_usage + usage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "run_id": self.run_id,
            "correlation_id": self.correlation_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "step_index": self.step_index,
            "incident": self.incident,
            "subtasks": [
                {
                    "id": s.id,
                    "title": s.title,
                    "agent": s.agent.value,
                    "detail": s.detail,
                    "status": s.status.value,
                }
                for s in self.subtasks
            ],
            "root_cause": _root_cause_dict(self.root_cause),
            "patch": _patch_dict(self.patch),
            "verification": _verification_dict(self.verification),
            "timeline": [
                {
                    "step_index": t.step_index,
                    "node": t.node.value,
                    "started_at": t.started_at,
                    "finished_at": t.finished_at,
                    "status": t.status.value,
                    "summary": t.summary,
                    "snapshot_ref": t.snapshot_ref,
                    "duration_seconds": t.duration_seconds,
                }
                for t in self.timeline
            ],
            "memory_snapshots": list(self.memory_snapshots),
            "token_usage": {
                "input_tokens": self.token_usage.input_tokens,
                "output_tokens": self.token_usage.output_tokens,
            },
            "verifier_attempt": self.verifier_attempt,
            "verifier_feedback": self.verifier_feedback,
            "hitl_approved": self.hitl_approved,
            "error": self.error,
        }


def _root_cause_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "hypothesis": value.hypothesis,
        "evidence": list(value.evidence),
        "confidence": value.confidence,
        "memory_refs": list(value.memory_refs),
    }


def _patch_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "patch_ref": value.patch_ref,
        "status": value.status.value,
        "summary": value.summary,
        "diff": value.diff,
        "review_reason": value.review_reason,
        "files": [
            {
                "path": pf.path,
                "old_content": pf.old_content,
                "new_content": pf.new_content,
            }
            for pf in value.files
        ],
    }


def _verification_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "ok": value.ok,
        "output": value.output,
        "failures": list(value.failures),
        "duration_seconds": value.duration_seconds,
        "attempt": value.attempt,
        "coverage": value.coverage,
    }


def create_initial_state(incident: dict[str, Any], *, incident_id: str | None = None) -> AgentState:
    if not incident_id:
        incident_id = str(incident.get("id") or f"inc_{uuid.uuid4().hex[:16]}")
    correlation_id = f"corr_{uuid.uuid4().hex[:16]}"
    return AgentState(
        incident_id=incident_id,
        incident=incident,
        correlation_id=correlation_id,
        status=GraphStatus.QUEUED,
    )


def execution_elapsed(state: AgentState, now: float | None = None) -> float:
    now = now or time.time()
    start = state.created_at
    return now - _parse_iso(start)


def _parse_iso(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0
