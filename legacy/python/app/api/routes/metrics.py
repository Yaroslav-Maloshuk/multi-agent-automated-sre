"""ROI + token telemetry endpoints for the Recharts dashboard."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_roi
from app.services.roi import ROILedger

router = APIRouter()


@router.get("/metrics/roi")
async def roi_summary(roi: ROILedger = Depends(get_roi)) -> dict[str, Any]:
    return {"summary": roi.summary(), "history": roi.token_history()}
