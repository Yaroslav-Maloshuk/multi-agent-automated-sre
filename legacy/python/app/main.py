"""FastAPI application factory — the async gateway.

Wires the event bus, hybrid memory, LLM client, warm sandbox pool, graph
executor, WS connection manager, ROI ledger and incident service into a single
app-scoped context, then exposes the REST + WebSocket contract surfaces.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.ws.events import router as ws_router
from app.api.ws.manager import ConnectionManager
from app.bus.redis_bus import build_event_bus
from app.collectors.runner import CollectorRunner
from app.core.config import Settings, get_settings
from app.core.errors import SandboxError, register_exception_handlers
from app.core.logging import configure_logging
from app.graph.approvals import build_approval_registry
from app.graph.executor import GraphExecutor
from app.llm.base import LLMClient
from app.llm.providers.factory import build_llm
from app.llm.tracing import TracedLLM
from app.memory.hybrid import build_memory
from app.sandbox.pool import build_sandbox_pool
from app.services.incident_service import IncidentService
from app.services.roi import build_roi

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)

    bus = build_event_bus(settings)
    await bus.start()

    memory = build_memory(settings)
    await memory.start()

    llm: LLMClient = app.state.llm_override or build_llm(settings)

    pool = build_sandbox_pool(settings)
    try:
        await pool.start()
    except SandboxError as exc:
        logger.warning("sandbox pool unavailable at startup: %s", exc)

    approvals = build_approval_registry(settings)
    executor = GraphExecutor(
        bus=bus, llm=llm, sandbox_pool=pool, memory=memory, settings=settings, approval_registry=approvals
    )
    roi = build_roi(settings)
    await roi.start()
    service = IncidentService(executor, bus, roi, settings, approvals, memory)
    await service.start()
    manager = ConnectionManager(bus, settings)
    await manager.start()

    runner = CollectorRunner(settings, service.create)
    await runner.start()

    app.state.bus = bus
    app.state.memory = memory
    app.state.sandbox_pool = pool
    app.state.llm = llm
    app.state.executor = executor
    app.state.approvals = approvals
    app.state.roi = roi
    app.state.incident_service = service
    app.state.ws_manager = manager
    app.state.collector_runner = runner

    logger.info(
        "gateway ready: env=%s provider=%s sandbox=%s executor=%s",
        settings.app_env.value,
        settings.llm_provider.value,
        pool.__class__.__name__,
        "celery" if settings.celery_enabled else "in-process",
    )
    try:
        yield
    finally:
        await runner.stop()
        await service.stop()
        await manager.stop()
        await pool.stop()
        await memory.close()
        await roi.close()
        if isinstance(llm, TracedLLM):
            llm.flush()
        await bus.stop()


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Multi-Agent Automated SRE Workspace — autonomous self-healing engine",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.llm_override = llm

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router)
    app.include_router(ws_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "ws": "/ws/stream?channels=telemetry,incident:*",
        }

    return app


app = create_app()
