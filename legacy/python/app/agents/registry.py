"""Agent catalog and factory (used by the orchestrator and the API catalog
endpoint)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.base import BaseAgent
from app.agents.code_fixer import CodeFixerAgent
from app.agents.deploy import DeployAgent
from app.agents.log_analyzer import LogAnalyzerAgent
from app.agents.planner import PlannerAgent
from app.agents.verifier import VerifierAgent
from app.graph.state import AgentName


@dataclass(frozen=True, slots=True)
class AgentInfo:
    name: AgentName
    label: str
    description: str
    model_hint: str
    sandboxed: bool


AGENT_CATALOG: tuple[AgentInfo, ...] = (
    AgentInfo(
        AgentName.PLANNER,
        "Planner",
        "Decomposes an alert into actionable subtasks",
        "gpt-4o / claude-sonnet / local",
        sandboxed=False,
    ),
    AgentInfo(
        AgentName.LOG_ANALYZER,
        "Log Analyzer",
        "Hybrid pgvector+BM25 retrieval distills logs into a root-cause summary",
        "embedding + chat",
        sandboxed=False,
    ),
    AgentInfo(
        AgentName.CODE_FIXER,
        "Code Fixer",
        "Writes source patches inside an isolated sandbox, streams live diffs",
        "chat",
        sandboxed=True,
    ),
    AgentInfo(
        AgentName.VERIFIER,
        "Verifier",
        "Runs pytest + flake8 in the sandbox and drives the self-correction loop",
        "none (execution)",
        sandboxed=True,
    ),
    AgentInfo(
        AgentName.DEPLOY,
        "Deploy",
        "Applies an approved, verified patch to the CD pipeline",
        "none",
        sandboxed=False,
    ),
)

_AGENT_CLASSES: dict[AgentName, type[BaseAgent]] = {
    AgentName.PLANNER: PlannerAgent,
    AgentName.LOG_ANALYZER: LogAnalyzerAgent,
    AgentName.CODE_FIXER: CodeFixerAgent,
    AgentName.VERIFIER: VerifierAgent,
    AgentName.DEPLOY: DeployAgent,
}


def build_agent(name: AgentName) -> BaseAgent:
    try:
        return _AGENT_CLASSES[name]()
    except KeyError as exc:
        raise ValueError(f"unknown agent: {name}") from exc


def agent_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": info.name.value,
            "label": info.label,
            "description": info.description,
            "model_hint": info.model_hint,
            "sandboxed": info.sandboxed,
        }
        for info in AGENT_CATALOG
    ]
