"""Agent catalog endpoint."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.agents.registry import agent_catalog
from app.graph.graph import GRAPH_SPEC

router = APIRouter()


@router.get("/agents")
async def list_agents() -> dict[str, Any]:
    agents = agent_catalog()
    for info in agents:
        spec = next((s for s in GRAPH_SPEC.values() if s.name.value == info["name"]), None)
        if spec:
            info["deps"] = [dep.value for dep in spec.deps]
    return {"agents": agents}
