"""Sandbox pool control surface."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_pool
from app.models.schemas import SandboxWarmRequest
from app.sandbox.pool import SandboxPool

router = APIRouter()


@router.get("/sandboxes")
async def sandbox_stats(pool: SandboxPool = Depends(get_pool)) -> dict:
    stats = await pool.stats()
    return {
        "backend": stats.backend,
        "warm": stats.warm,
        "in_use": stats.in_use,
        "max_size": stats.max_size,
    }


@router.post("/sandboxes/warm")
async def warm_sandboxes(payload: SandboxWarmRequest | None = None, pool: SandboxPool = Depends(get_pool)) -> dict:
    count = payload.count if payload else None
    created = await pool.warm(count)
    stats = await pool.stats()
    return {"created": created, "warm": stats.warm, "in_use": stats.in_use}
