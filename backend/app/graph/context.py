"""Execution context passed to every node handler and agent.

Carries the live dependencies plus the current immutable state snapshot and
publishing helpers so agents never touch infrastructure directly.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.bus.redis_bus import EventBus
from app.bus.schemas import Channel, EventEnvelope, EventType, HITLReview
from app.core.config import Settings
from app.graph.state import AgentState
from app.llm.base import LLMClient
from app.memory.hybrid import MemoryStore
from app.sandbox.pool import SandboxPool

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """Per-run dependency bundle. `state` is replaced after every transition."""

    bus: EventBus
    llm: LLMClient
    sandbox_pool: SandboxPool
    memory: MemoryStore
    settings: Settings
    approval_registry: dict[str, asyncio.Future[HITLReview]]
    state: AgentState

    # ── Event emission ──────────────────────────────────────────────────

    def _envelope(self, type_: EventType, payload: dict[str, Any], channel: str | None) -> EventEnvelope:
        return EventEnvelope(
            type=type_,
            channel=channel or Channel.incident(self.state.incident_id),
            correlation_id=self.state.correlation_id,
            incident_id=self.state.incident_id,
            run_id=self.state.run_id,
            payload=payload,
        )

    async def emit(self, type_: EventType, channel: str | None = None, **payload: Any) -> None:
        await self.bus.publish(self._envelope(type_, payload, channel))

    async def emit_log(self, line: str, level: str = "info", **extra: Any) -> None:
        logger.debug("agent log: %s", line)
        payload: dict[str, Any] = {"line": line, "level": level, "step_index": self.state.step_index, **extra}
        await self.emit(EventType.AGENT_LOG, **payload)

    async def emit_tokens(self, usage: Any) -> None:
        await self.emit(
            EventType.AGENT_TOKEN_USAGE,
            payload={
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
        )
