"""Topology of the stateful multi-agent graph.

This is a static DAG definition; dynamic behavior (retries, HITL resume,
interrupts) is handled by the executor. The topology intentionally mirrors
the UI's React Flow rendering one-to-one.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.graph.context import AgentContext
from app.graph.state import AgentName, AgentState


@dataclass(frozen=True, slots=True)
class NodeSpec:
    name: AgentName
    deps: tuple[AgentName, ...]
    handler_key: str
    label: str


GRAPH_SPEC: dict[AgentName, NodeSpec] = {
    AgentName.ALERT_INGEST: NodeSpec(
        name=AgentName.ALERT_INGEST,
        deps=(),
        handler_key="ingest",
        label="Alert Ingest",
    ),
    AgentName.PLANNER: NodeSpec(
        name=AgentName.PLANNER,
        deps=(AgentName.ALERT_INGEST,),
        handler_key="planner",
        label="Planner",
    ),
    AgentName.LOG_ANALYZER: NodeSpec(
        name=AgentName.LOG_ANALYZER,
        deps=(AgentName.PLANNER,),
        handler_key="log_analyzer",
        label="Log Analyzer",
    ),
    AgentName.CODE_FIXER: NodeSpec(
        name=AgentName.CODE_FIXER,
        deps=(AgentName.PLANNER, AgentName.LOG_ANALYZER),
        handler_key="code_fixer",
        label="Code Fixer",
    ),
    AgentName.HITL_GATE: NodeSpec(
        name=AgentName.HITL_GATE,
        deps=(AgentName.CODE_FIXER,),
        handler_key="hitl_gate",
        label="Human Gate",
    ),
    AgentName.VERIFIER: NodeSpec(
        name=AgentName.VERIFIER,
        deps=(AgentName.HITL_GATE,),
        handler_key="verifier",
        label="Verifier",
    ),
    AgentName.DEPLOY: NodeSpec(
        name=AgentName.DEPLOY,
        deps=(AgentName.VERIFIER,),
        handler_key="deploy",
        label="Deploy",
    ),
}

# Root nodes that may be scheduled first.
ROOT_NODES = tuple(spec.name for spec in GRAPH_SPEC.values() if not spec.deps)

# Ordered by dependency order — used for deterministic parallel merge.
NODE_ORDER = tuple(spec.name for spec in sorted(GRAPH_SPEC.values(), key=lambda s: s.name.value))


# Handler signatures: async (ctx, state) -> (new_state, summary)
NodeHandler = Callable[[AgentContext, AgentState], Awaitable[tuple[AgentState, str]]]


def build_node_map() -> dict[str, NodeHandler]:
    """Wire handler keys to concrete async node functions.

    Imported lazily inside the factory to keep the topology module free of
    heavy agent imports at import-time.
    """
    from app.graph import nodes

    return {
        "ingest": nodes.alert_ingest,
        "planner": nodes.planner_node,
        "log_analyzer": nodes.log_analyzer_node,
        "code_fixer": nodes.code_fixer_node,
        "hitl_gate": nodes.hitl_gate_node,
        "verifier": nodes.verifier_node,
        "deploy": nodes.deploy_node,
    }
