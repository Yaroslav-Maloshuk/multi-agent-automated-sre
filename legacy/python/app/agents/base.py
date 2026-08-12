"""Base agent contract.

Agents are stateless actors: all dependencies arrive via `AgentContext` and
the immutable `AgentState` is threaded through and returned. Every LLM
round-trip is accounted into the token ledger and streamed as telemetry.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.bus.schemas import EventType
from app.graph.context import AgentContext
from app.graph.state import AgentName, AgentState, TokenUsage
from app.llm.base import _extract_json


class BaseAgent(ABC):
    name: AgentName

    @abstractmethod
    async def run(self, ctx: AgentContext, state: AgentState) -> AgentState:
        """Advance the incident state via this agent's contribution."""
        raise NotImplementedError

    # ── Shared tooling ──────────────────────────────────────────────────

    async def complete(self, ctx: AgentContext, state: AgentState, system: str, user: str) -> tuple[str, AgentState]:
        """Single chat round-trip with token accounting and telemetry."""
        response = await ctx.llm.complete(system, user)
        state = state.add_tokens(TokenUsage(response.input_tokens, response.output_tokens))
        await ctx.emit_tokens(response)
        await ctx.emit_log(f"[{self.name.value}] llm round-trip ({response.output_tokens} tokens out)")
        return response.text, state

    async def complete_json(self, ctx: AgentContext, state: AgentState, system: str, user: str) -> tuple[dict[str, Any], AgentState]:
        response = await ctx.llm.complete(system, user)
        data = _extract_json(response.text)
        state = state.add_tokens(TokenUsage(response.input_tokens, response.output_tokens))
        await ctx.emit_tokens(response)
        await ctx.emit_log(f"[{self.name.value}] parsed json plan ({response.output_tokens} tokens out)")
        return data, state

    async def emit_step(self, ctx: AgentContext, title: str, detail: str = "") -> None:
        await ctx.emit(
            EventType.AGENT_STEP,
            agent=self.name.value,
            step={"title": title, "detail": detail},
        )
