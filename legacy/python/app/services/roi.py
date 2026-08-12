"""Business ROI ledger: translates agent latency + token spend into dollars.

`ROIEntry` records, per incident: resolved-by-AI duration, token cost, and the
savings vs. the human MTTR baseline at a loaded hourly rate.

The ledger is *durable*: in production entries are persisted to Postgres
(`roi_ledger` table via raw asyncpg, mirroring `PostgresHybridMemory`) and
hydrated on startup, so summary + history survive process restarts. The
in-memory implementation below keeps the identical interface for the test
suite, which runs with zero external services.
"""
from __future__ import annotations

from dataclasses import dataclass
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ROIEntry:
        return cls(
            incident_id=str(data["incident_id"]),
            status=str(data["status"]),
            duration_seconds=float(data["duration_seconds"]),
            human_mttr_seconds=int(data["human_mttr_seconds"]),
            input_tokens=int(data["input_tokens"]),
            output_tokens=int(data["output_tokens"]),
            token_cost=float(data["token_cost"]),
            saved_seconds=float(data["saved_seconds"]),
            saved_amount=float(data["saved_amount"]),
        )


class ROILedger:
    """Async ROI ledger. `record` is awaited by run orchestration."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._entries: dict[str, ROIEntry] = {}
        self._historical: list[ROIEntry] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def record(self, state: AgentState) -> ROIEntry:
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


class PostgresROILedger(ROILedger):
    """Production ledger: rows in Postgres, hydrated into memory on start.

    Reads stay synchronous (served from the in-memory cache); only writes hit
    the database. The table is created idempotently on `start`.
    """

    TABLE = "roi_ledger"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._url = settings.database_url
        self._pool: Any = None

    async def start(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=4)
        await self._pool.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE} (
                incident_id        TEXT PRIMARY KEY,
                status             TEXT NOT NULL,
                duration_seconds   DOUBLE PRECISION NOT NULL,
                human_mttr_seconds INTEGER NOT NULL,
                input_tokens       INTEGER NOT NULL,
                output_tokens      INTEGER NOT NULL,
                token_cost         DOUBLE PRECISION NOT NULL,
                saved_seconds      DOUBLE PRECISION NOT NULL,
                saved_amount       DOUBLE PRECISION NOT NULL,
                recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await self._hydrate()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _hydrate(self) -> None:
        assert self._pool is not None
        rows = await self._pool.fetch(
            f"SELECT * FROM {self.TABLE} ORDER BY recorded_at ASC"
        )
        for row in rows:
            entry = ROIEntry.from_dict(dict(row))
            self._entries[entry.incident_id] = entry
            self._historical.append(entry)

    async def record(self, state: AgentState) -> ROIEntry:
        if state.status not in {GraphStatus.COMPLETED, GraphStatus.FAILED, GraphStatus.CANCELLED}:
            return await super().record(state)
        entry = await super().record(state)
        assert self._pool is not None
        await self._pool.execute(
            f"""
            INSERT INTO {self.TABLE} (
                incident_id, status, duration_seconds, human_mttr_seconds,
                input_tokens, output_tokens, token_cost, saved_seconds, saved_amount
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (incident_id) DO UPDATE SET
                status = EXCLUDED.status,
                duration_seconds = EXCLUDED.duration_seconds,
                human_mttr_seconds = EXCLUDED.human_mttr_seconds,
                input_tokens = EXCLUDED.input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                token_cost = EXCLUDED.token_cost,
                saved_seconds = EXCLUDED.saved_seconds,
                saved_amount = EXCLUDED.saved_amount
            """,
            entry.incident_id,
            entry.status,
            entry.duration_seconds,
            entry.human_mttr_seconds,
            entry.input_tokens,
            entry.output_tokens,
            entry.token_cost,
            entry.saved_seconds,
            entry.saved_amount,
        )
        return entry


def build_roi(settings: Settings) -> ROILedger:
    if settings.is_test:
        return ROILedger(settings)
    return PostgresROILedger(settings)


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
