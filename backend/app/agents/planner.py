"""Planner agent: breaks an alert into actionable subtasks.

Uses the LLM when available and degrades to a deterministic, severity-aware
rule-based plan so the engine never stalls on model availability.
"""
from __future__ import annotations

import json
from typing import Any

from app.agents.base import BaseAgent
from app.bus.schemas import EventType
from app.core.errors import LLMError
from app.graph.context import AgentContext
from app.graph.state import AgentName, AgentState, Subtask, SubtaskStatus

_SYSTEM_PROMPT = (
    "You are a senior SRE planner. Given an infrastructure alert, produce a "
    "concise, actionable JSON plan. Respond ONLY with JSON of the shape:\n"
    '{"subtasks": [{"title": str, "agent": "log_analyzer"|"code_fixer"|"verifier", '
    '"detail": str}]}\n'
    "Between 2 and 5 subtasks."
)


class PlannerAgent(BaseAgent):
    name = AgentName.PLANNER

    async def run(self, ctx: AgentContext, state: AgentState) -> AgentState:
        incident = state.incident
        await ctx.emit_log(f"[planner] decomposing alert '{incident.get('title', '')}'")

        plan: list[dict[str, Any]] | None = None
        try:
            data, state = await self.complete_json(
                ctx,
                state,
                _SYSTEM_PROMPT,
                json.dumps(incident, ensure_ascii=False, default=str),
            )
            raw = data.get("subtasks")
            if isinstance(raw, list) and raw:
                plan = raw
        except LLMError as exc:
            await ctx.emit_log(f"[planner] llm unavailable, using rule-based plan ({exc})", level="warn")

        subtasks = self._materialize(incident, plan) if plan else self._rule_based(incident)
        for subtask in subtasks:
            await ctx.emit(
                EventType.AGENT_STEP,
                agent=self.name.value,
                step={"title": subtask.title, "detail": subtask.detail, "status": subtask.status.value},
            )
        await ctx.emit_log(f"[planner] generated {len(subtasks)} subtasks")
        return state.replace(subtasks=tuple(subtasks))

    def _materialize(self, incident: dict[str, Any], plan: list[dict[str, Any]]) -> list[Subtask]:
        subtasks: list[Subtask] = []
        for idx, item in enumerate(plan, start=1):
            agent = _coerce_agent(item.get("agent"))
            if agent is None:
                continue
            subtasks.append(
                Subtask(
                    id=f"st-{idx}",
                    title=str(item.get("title", "subtask")),
                    agent=agent,
                    detail=str(item.get("detail", "")),
                    status=SubtaskStatus.QUEUED,
                )
            )
        if not subtasks:
            return self._rule_based(incident)
        return subtasks

    def _rule_based(self, incident: dict[str, Any]) -> list[Subtask]:
        title = str(incident.get("title", "incident"))
        severity = str(incident.get("severity", "high")).lower()
        return [
            Subtask("st-1", "Correlate production logs", AgentName.LOG_ANALYZER, f"trace: {title}"),
            Subtask(
                "st-2",
                "Reproduce and patch root cause",
                AgentName.CODE_FIXER,
                f"severity={severity}",
            ),
            Subtask("st-3", "Verify patch under pytest", AgentName.VERIFIER, "gate: green build"),
        ]


def _coerce_agent(value: Any) -> AgentName | None:
    try:
        candidate = AgentName(str(value))
    except ValueError:
        return None
    return candidate if candidate in {AgentName.LOG_ANALYZER, AgentName.CODE_FIXER, AgentName.VERIFIER} else None
