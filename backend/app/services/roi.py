"""Business ROI ledger: translates agent latency + token spend into dollars.

`ROIEntry` records, per incident: resolved-by-AI duration, token cost, and the
savings vs. the human MTTR baseline at a loaded hourly rate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.graph.state import AgentState, GraphStatus


@dataclass(frozen=True, slots=True)
class ROIEntry:
    incident_id: str
    status: str
    duration_seconds: float
    human_mttr_seconds: int
    input_tokens: int
    output_tokens: int
    token_cost: float
    saved_seconds: float
    saved_amount: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 2),
            "human_mttr_seconds": self.human_mttr_seconds,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "token_cost": round(self.token_cost, 4),
            "saved_seconds": round(self.saved_seconds, 2),
            "saved_amount": round(self.saved_amount, 2),
        }


class ROILedger:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._entries: dict[str, ROIEntry] = {}
        self._historical: list[ROIEntry] = []

    def record(self, state: AgentState) -> ROIEntry:
        if state.status not in {GraphStatus.COMPLETED, GraphStatus.FAILED, GraphStatus.CANCELLED}:
            return self._entries.get(state.incident_id, _zero_entry(self._settings, state))

        duration = _elapsed(state)
        human_seconds = self._settings.human_mttr_seconds
        saved_seconds = max(0.0, human_seconds - duration) if state.status is GraphStatus.COMPLETED else 0.0
        saved_hours = saved_seconds / 3600.0
        loaded_rate = self._settings.human_loaded_cost_per_hour

        token_cost = state.token_usage.cost(
            self._settings.token_price_per_1k_in,
            self._settings.token_price_per_1k_out,
        )
        labor_savings = saved_hours * loaded_rate
        saved_amount = max(0.0, labor_savings - token_cost)

        entry = ROIEntry(
            incident_id=state.incident_id,
            status=state.status.value,
            duration_seconds=duration,
            human_mttr_seconds=human_seconds,
            input_tokens=state.token_usage.input_tokens,
            output_tokens=state.token_usage.output_tokens,
            token_cost=token_cost,
            saved_seconds=saved_seconds,
            saved_amount=saved_amount,
        )
        self._entries[state.incident_id] = entry
        self._historical.append(entry)
        return entry

    def summary(self) -> dict[str, Any]:
        entries = list(self._entries.values())
        return {
            "total_incidents": len(entries),
            "resolved": sum(1 for e in entries if e.status == "COMPLETED"),
            "total_tokens": sum(e.input_tokens + e.output_tokens for e in entries),
            "total_token_cost": round(sum(e.token_cost for e in entries), 4),
            "total_saved_amount": sum(e.saved_amount for e in entries),
            "total_saved_seconds": round(sum(e.saved_seconds for e in entries), 2),
            "avg_duration_seconds": round(_avg([e.duration_seconds for e in entries]), 2),
            "human_mttr_seconds": self._settings.human_mttr_seconds,
            "loaded_cost_per_hour": self._settings.human_loaded_cost_per_hour,
        }

    def token_history(self, limit: int = 200) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._historical[-limit:]]

    def for_incident(self, incident_id: str) -> dict[str, Any] | None:
        entry = self._entries.get(incident_id)
        return entry.to_dict() if entry else None


def _elapsed(state: AgentState) -> float:
    from datetime import UTC, datetime

    try:
        start = datetime.fromisoformat(state.created_at)
        end = datetime.fromisoformat(state.completed_at or "") if state.completed_at else datetime.now(UTC)
        return max(0.0, (end - start).total_seconds())
    except ValueError:
        return 0.0


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _zero_entry(settings: Settings, state: AgentState) -> ROIEntry:
    return ROIEntry(
        incident_id=state.incident_id,
        status=state.status.value,
        duration_seconds=0.0,
        human_mttr_seconds=settings.human_mttr_seconds,
        input_tokens=state.token_usage.input_tokens,
        output_tokens=state.token_usage.output_tokens,
        token_cost=0.0,
        saved_seconds=0.0,
        saved_amount=0.0,
    )
