"""FastAPI dependency helpers — access app-scoped singletons."""
from __future__ import annotations

from fastapi import Request

from app.api.ws.manager import ConnectionManager
from app.bus.redis_bus import EventBus
from app.graph.executor import GraphExecutor
from app.memory.hybrid import MemoryStore
from app.sandbox.pool import SandboxPool
from app.services.incident_service import IncidentService
from app.services.roi import ROILedger


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus


def get_executor(request: Request) -> GraphExecutor:
    return request.app.state.executor


def get_pool(request: Request) -> SandboxPool:
    return request.app.state.sandbox_pool


def get_memory(request: Request) -> MemoryStore:
    return request.app.state.memory


def get_ws_manager(request: Request) -> ConnectionManager:
    return request.app.state.ws_manager


def get_incident_service(request: Request) -> IncidentService:
    return request.app.state.incident_service


def get_roi(request: Request) -> ROILedger:
    return request.app.state.roi
