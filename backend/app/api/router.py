"""API v1 router aggregation."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import agents, collectors, health, incidents, metrics, sandboxes

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(incidents.router, tags=["incidents"])
api_router.include_router(agents.router, tags=["agents"])
api_router.include_router(sandboxes.router, tags=["sandboxes"])
api_router.include_router(metrics.router, tags=["metrics"])
api_router.include_router(collectors.router, tags=["collectors"])
