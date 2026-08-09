"""Health probes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_bus, get_pool
from app.bus.redis_bus import EventBus
from app.sandbox.pool import SandboxPool

router = APIRouter()


@router.get("/health")
async def health(request: Request, bus: EventBus = Depends(get_bus), pool: SandboxPool = Depends(get_pool)) -> dict[str, Any]:
    deps = {
        "bus": "ok" if await bus.healthy() else "unreachable",
        "sandbox_pool": "ok" if pool else "missing",
    }
    return {
        "status": "ok" if all(v == "ok" for v in deps.values()) else "degraded",
        "version": request.app.state.settings.app_version,
        "environment": request.app.state.settings.app_env.value,
        "dependencies": deps,
    }
