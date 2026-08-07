"""Domain error hierarchy and FastAPI exception handlers.

A single envelope keeps every failure — DB, Redis, sandbox, LLM — consistent
across REST and WebSocket surfaces.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base application error with a stable machine-readable code."""

    code: str = "app_error"
    status_code: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404


class ConflictError(AppError):
    code = "conflict"
    status_code = 409


class ValidationError(AppError):
    code = "validation_error"
    status_code = 422


class GraphError(AppError):
    code = "graph_error"
    status_code = 500


class GraphNotReadyError(GraphError):
    """Raised when an operation targets a graph run that is not in the
    expected state (e.g. reviewing before a patch exists)."""
    code = "graph_not_ready"
    status_code = 409


class SandboxError(AppError):
    code = "sandbox_error"
    status_code = 503


class SandboxUnavailableError(SandboxError):
    code = "sandbox_unavailable"
    status_code = 503


class LLMError(AppError):
    code = "llm_error"
    status_code = 502


class BusError(AppError):
    code = "bus_error"
    status_code = 503


def _error_body(exc: AppError) -> dict[str, Any]:
    return {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("request failed", log_extra_fields(exc))
        return JSONResponse(status_code=exc.status_code, content=_error_body(exc))

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception during request handling")
        wrapped = AppError("internal server error")
        return JSONResponse(status_code=500, content=_error_body(wrapped))


def log_extra_fields(exc: AppError) -> dict[str, Any]:
    return {"extra_fields": {"code": exc.code, "details": exc.details}}
