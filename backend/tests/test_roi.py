"""ROI ledger math."""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from app.graph.state import AgentState, GraphStatus, TokenUsage


def _completed_state() -> AgentState:
    now = datetime.now(UTC)
    return AgentState(
        incident_id="inc_1",
        incident={},
        correlation_id="corr_1",
        run_id="run_1",
        status=GraphStatus.COMPLETED,
        created_at=(now - timedelta(seconds=42)).isoformat(),
        completed_at=now.isoformat(),
        token_usage=TokenUsage(input_tokens=1000, output_tokens=500),
    )


def test_roi_savings(settings, roi) -> None:
    entry = asyncio.run(roi.record(_completed_state()))

    assert entry.duration_seconds == pytest.approx(42)
    # human_mttr = 2100s → saved = 2058s = 0.5717h × $150/h = $85.75
    # tokens: 1000/1000×0.005 + 500/1000×0.015 = $0.0125
    assert entry.token_cost == pytest.approx(0.0125)
    assert entry.saved_amount == pytest.approx(85.75 - 0.0125, abs=1e-6)


def test_roi_failed_has_no_savings(settings, roi) -> None:
    now = datetime.now(UTC)
    state = AgentState(
        incident_id="inc_2",
        incident={},
        correlation_id="corr_2",
        status=GraphStatus.FAILED,
        created_at=(now - timedelta(seconds=30)).isoformat(),
        completed_at=now.isoformat(),
        token_usage=TokenUsage(input_tokens=500, output_tokens=250),
    )
    entry = asyncio.run(roi.record(state))
    assert entry.saved_amount == 0
    assert entry.status == "FAILED"


def test_roi_summary_aggregates(roi) -> None:
    asyncio.run(roi.record(dataclasses.replace(_completed_state(), incident_id="inc_1")))
    asyncio.run(roi.record(dataclasses.replace(_completed_state(), incident_id="inc_2")))
    summary = roi.summary()
    assert summary["total_incidents"] == 2
    assert summary["resolved"] == 2
    assert summary["total_saved_amount"] == pytest.approx(2 * (85.75 - 0.0125), abs=1e-6)
