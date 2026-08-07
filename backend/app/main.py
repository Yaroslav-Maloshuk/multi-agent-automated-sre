"""FastAPI application factory — the async gateway.

Wires the event bus, hybrid memory, LLM client, warm sandbox pool, graph
executor, WS connection manager, ROI ledger and incident service into a single
app-scoped context, then exposes the REST + WebSocket contract surfaces.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.ws.events import router as ws_router
from app.api.ws.manager import ConnectionManager
from app.bus.redis_bus import build_event_bus
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers, SandboxError
from app.core.logging import configure_logging
from app.graph.executor import GraphExecutor
from app.llm.base import LLMClient
from app.llm.providers.factory import build_llm
from app.memory.hybrid import build_memory
from app.sandbox.pool import build_sandbox_pool
from app.services.incident_service import IncidentService
from app.services.roi import ROILedger

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
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

    executor = GraphExecutor(bus=bus, llm=llm, sandbox_pool=pool, memory=memory, settings=settings)
    roi = ROILedger(settings)
    service = IncidentService(executor, bus, roi, settings)
    manager = ConnectionManager(bus, settings)
    await manager.start()

    app.state.bus = bus
    app.state.memory = memory
    app.state.sandbox_pool = pool
    app.state.llm = llm
    app.state.executor = executor
    app.state.roi = roi
    app.state.incident_service = service
    app.state.ws_manager = manager

    logger.info(
        "gateway ready: env=%s provider=%s sandbox=%s",
        settings.app_env.value,
        settings.llm_provider.value,
        pool.__class__.__name__,
    )
    try:
        yield
    finally:
        await manager.stop()
        await pool.stop()
        await memory.close()
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
    async def root() -> dict:
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "ws": "/ws/stream?channels=telemetry,incident:*",
        }

    return app


app = create_app()
