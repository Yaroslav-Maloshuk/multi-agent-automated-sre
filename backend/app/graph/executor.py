"""The async DAG executor — heartbeat of the autonomous engine.

Responsibilities:
* Schedule nodes respecting dependencies (fan-out branches execute
  concurrently on copy-on-write state with a deterministic merge).
* Publish every transition to the event bus (time-travel / UI state sync).
* Enforce the Human-in-the-Loop gate: block on a signed review future and
  resume (approve / reject / modify) via `submit_review`.
* Run the verifier self-correction loop up to `max_verifier_attempts`.
* Honour interrupts so a cancelled run never leaves zombie state.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Any

from app.bus.redis_bus import EventBus
from app.bus.schemas import EventType, HITLReview
from app.core.config import Settings
from app.core.errors import ConflictError, GraphError, GraphNotReadyError
from app.graph.context import AgentContext
from app.graph.graph import NODE_ORDER, GRAPH_SPEC, NodeHandler
from app.graph.state import AgentName, AgentState, GraphStatus, create_initial_state
from app.llm.base import LLMClient
from app.memory.hybrid import MemoryStore
from app.sandbox.pool import SandboxPool

logger = logging.getLogger(__name__)

_TERMINAL = {GraphStatus.COMPLETED, GraphStatus.FAILED, GraphStatus.CANCELLED, GraphStatus.INTERRUPTED}


class GraphExecutor:
    """Executes one incident-resolution run per task; safe for concurrent runs."""

    def __init__(
        self,
        *,
        bus: EventBus,
        llm: LLMClient,
        sandbox_pool: SandboxPool,
        memory: MemoryStore,
        settings: Settings,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._pool = sandbox_pool
        self._memory = memory
        self._settings = settings
        self._handlers: dict[str, NodeHandler] = self._load_handlers()
        self._approvals: dict[str, asyncio.Future[HITLReview]] = {}
        self._tasks: dict[str, asyncio.Task[AgentState]] = {}
        self._run_by_incident: dict[str, str] = {}
        self._latest_state: dict[str, AgentState] = {}

    @staticmethod
    def _load_handlers() -> dict[str, NodeHandler]:
        from app.graph.graph import build_node_map

        return build_node_map()

    # ── Public entrypoints ──────────────────────────────────────────────

    def submit_review(self, review: HITLReview) -> None:
        """Resolve a pending gate future, from the WS handler or REST route."""
        fut = self._approvals.get(review.incident_id)
        if fut is None or fut.done():
            raise GraphNotReadyError(
                f"no pending approval for incident {review.incident_id}",
                details={"incident_id": review.incident_id},
            )
        fut.set_result(review)

    async def interrupt(self, incident_id: str) -> AgentState:
        run_id = self._run_by_incident.get(incident_id)
        if not run_id:
            raise GraphNotReadyError("run not found", details={"incident_id": incident_id})
        task = self._tasks.get(run_id)
        fut = self._approvals.get(incident_id)
        if fut is not None and not fut.done():
            fut.cancel()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        state = self._latest_state.get(incident_id)
        if state is not None:
            interrupted = state.finish(GraphStatus.INTERRUPTED)
            self._latest_state[incident_id] = interrupted
            return interrupted
        raise GraphNotReadyError("run has no recoverable state", details={"incident_id": incident_id})

    async def run(self, incident: dict[str, Any], *, incident_id: str | None = None) -> AgentState:
        if incident_id and incident_id in self._run_by_incident:
            raise ConflictError("incident already has an active run", details={"incident_id": incident_id})
        state = create_initial_state(incident, incident_id=incident_id)
        self._run_by_incident[state.incident_id] = state.run_id
        loop = asyncio.get_running_loop()
        self._approvals[state.incident_id] = loop.create_future()
        task = asyncio.create_task(self._execute_run(state), name=f"graph-{state.run_id}")
        self._tasks[state.run_id] = task
        try:
            return await task
        finally:
            self._tasks.pop(state.run_id, None)
            self._run_by_incident.pop(state.incident_id, None)
            self._approvals.pop(state.incident_id, None)

    # ── Run pipeline ────────────────────────────────────────────────────

    async def _execute_run(self, initial: AgentState) -> AgentState:
        ctx = AgentContext(
            bus=self._bus,
            llm=self._llm,
            sandbox_pool=self._pool,
            memory=self._memory,
            settings=self._settings,
            approval_registry=self._approvals,
            state=initial,
        )
        state = initial.replace(status=GraphStatus.RUNNING)
        await ctx.emit(EventType.GRAPH_STARTED, incident=state.incident, run_id=state.run_id)
        await ctx.emit(EventType.GRAPH_STATUS_CHANGED, status=state.status.value)

        try:
            try:
                state = await self._schedule(ctx, state)
            except asyncio.CancelledError:
                state = state.finish(GraphStatus.INTERRUPTED)
                await ctx.emit(EventType.GRAPH_INTERRUPTED, run_id=state.run_id)
                await ctx.emit(EventType.GRAPH_STATUS_CHANGED, status=state.status.value)
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("graph run failed")
                state = state.replace(error=str(exc)).finish(GraphStatus.FAILED)
                await ctx.emit(EventType.GRAPH_FAILED, error=str(exc))
                await ctx.emit(EventType.GRAPH_STATUS_CHANGED, status=state.status.value)

            if state.status is GraphStatus.RUNNING and state.verification is not None and not state.verification.ok:
                # Verifier exhausted its retries without a green build.
                state = state.replace(
                    error=f"verification failed after {state.verifier_attempt} attempt(s)"
                ).finish(GraphStatus.FAILED)
            elif state.status is GraphStatus.RUNNING:
                state = state.replace(error="graph completed without a terminal status").finish(GraphStatus.FAILED)

            if state.status is GraphStatus.COMPLETED:
                await ctx.emit(
                    EventType.GRAPH_COMPLETED,
                    run_id=state.run_id,
                    duration_seconds=_run_duration(state),
                    token_usage={
                        "input_tokens": state.token_usage.input_tokens,
                        "output_tokens": state.token_usage.output_tokens,
                    },
                )
            elif state.status is GraphStatus.FAILED:
                await ctx.emit(EventType.GRAPH_FAILED, error=state.error)
            await ctx.emit(EventType.GRAPH_STATUS_CHANGED, status=state.status.value)
            return state
        finally:
            self._latest_state[state.incident_id] = state

    async def _schedule(self, ctx: AgentContext, state: AgentState) -> AgentState:
        pending: deque[AgentName] = deque([AgentName.ALERT_INGEST])
        completed: set[AgentName] = set()

        while pending:
            frontier = [n for n in pending if all(dep in completed for dep in GRAPH_SPEC[n].deps)]
            if not frontier:
                raise GraphError("graph scheduling stalled — dependency cycle detected")
            frontier.sort(key=lambda n: NODE_ORDER.index(n))
            for name in frontier:
                pending.remove(name)

            outcomes = await asyncio.gather(*(self._execute_node(ctx, name, state) for name in frontier))
            if len(outcomes) == 1:
                state = outcomes[0]
            else:
                # Copy-on-write merge: the most-forward node in DAG order wins.
                winner = max(range(len(frontier)), key=lambda i: NODE_ORDER.index(frontier[i]))
                state = outcomes[winner]
                state = state.record_step(frontier[winner], f"{GRAPH_SPEC[frontier[winner]].label} complete")

            completed.update(frontier)
            last_node = frontier[-1]
            if state.status in _TERMINAL:
                break

            # Verifier self-correction loop: re-queue the fixer with feedback.
            # The verifier is deliberately re-run in a LATER round (never in
            # the same frontier — it would race the new patch), so both nodes
            # are dropped from `completed` to allow re-enqueueing.
            if (
                last_node is AgentName.VERIFIER
                and state.verification is not None
                and not state.verification.ok
                and state.verifier_attempt < self._settings.graph_max_verifier_attempts
            ):
                logger.info(
                    "verification failed, retrying fixer (attempt %s/%s)",
                    state.verifier_attempt + 1,
                    self._settings.graph_max_verifier_attempts,
                )
                completed.discard(AgentName.CODE_FIXER)
                completed.discard(AgentName.VERIFIER)
                pending.append(AgentName.CODE_FIXER)
                continue

            # Enqueue newly-satisfied dependents (deploy blocked while a
            # verification failure is outstanding).
            verification_blocked = state.verification is not None and not state.verification.ok
            for node, spec in GRAPH_SPEC.items():
                if node in completed or node in pending:
                    continue
                if node is AgentName.DEPLOY and verification_blocked:
                    continue
                if all(dep in completed for dep in spec.deps):
                    pending.append(node)

        return state

    async def _execute_node(self, ctx: AgentContext, name: AgentName, base: AgentState) -> AgentState:
        spec = GRAPH_SPEC[name]
        handler = self._handlers[spec.handler_key]
        ctx.state = base
        await ctx.emit(EventType.GRAPH_STEP_STARTED, node=name.value, label=spec.label, step_index=base.step_index + 1)
        await ctx.emit(EventType.AGENT_STARTED, agent=name.value, label=spec.label)

        result, summary = await handler(ctx, base)
        result = result.record_step(name, summary)

        try:
            snapshot_ref = await self._memory.snapshot(
                run_id=result.run_id,
                step_index=result.step_index,
                blob={"node": name.value, "summary": summary, "status": result.status.value},
            )
            result = result.replace(memory_snapshots=result.memory_snapshots + (snapshot_ref,))
        except Exception:  # noqa: BLE001 — memory snapshots are best-effort
            logger.warning("memory snapshot failed for run %s", result.run_id)

        await ctx.emit(
            EventType.GRAPH_STEP_COMPLETED,
            node=name.value,
            step_index=result.step_index,
            status=result.status.value,
            summary=summary,
        )
        await ctx.emit(EventType.GRAPH_STATUS_CHANGED, status=result.status.value)
        return result


def _run_duration(state: AgentState) -> float:
    try:
        start = datetime.fromisoformat(state.created_at)
        end = datetime.fromisoformat(state.completed_at or "")
        return max(0.0, (end - start).total_seconds())
    except ValueError:
        return 0.0
