"""Structured, correlation-aware JSON logging.

Every log record carries the current correlation / incident / run ids so a
single grep reconstructs an entire agent trajectory.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

import orjson

# Context carried through async tasks so child logs inherit run context.
correlation_id_ctx: ContextVar[str | None] = ContextVar("correlation_id", default=None)
incident_id_ctx: ContextVar[str | None] = ContextVar("incident_id", default=None)
run_id_ctx: ContextVar[str | None] = ContextVar("run_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, ctx in (
            ("correlation_id", correlation_id_ctx),
            ("incident_id", incident_id_ctx),
            ("run_id", run_id_ctx),
        ):
            value = ctx.get()
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return orjson.dumps(payload).decode()


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    # Keep noisy third-party loggers bounded.
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_extra(**fields: Any) -> dict[str, Any]:
    return {"extra_fields": fields}
