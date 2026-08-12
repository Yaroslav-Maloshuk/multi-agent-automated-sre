"""Graph node handlers.

Each handler is `async (ctx, state) -> (AgentState, summary)` and is wired to
the topology in `graph.graph.build_node_map`. The HITL gate is a first-class
interrupt point: it blocks the run until a signed review arrives.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace as dc_replace

from app.agents.code_fixer import CodeFixerAgent
from app.agents.deploy import DeployAgent
from app.agents.log_analyzer import LogAnalyzerAgent
from app.agents.planner import PlannerAgent
from app.agents.verifier import VerifierAgent
from app.bus.schemas import EventType, HITLReview, ReviewAction
from app.core.errors import GraphError
from app.graph.context import AgentContext
from app.graph.state import AgentState, GraphStatus, Patch, PatchFile, PatchStatus


async def alert_ingest(ctx: AgentContext, state: AgentState) -> tuple[AgentState, str]:
    incident = state.incident
    await ctx.emit_log(f"[ingest] alert '{incident.get('title', '')}' severity={incident.get('severity', 'n/a')}")
    return state.replace(status=GraphStatus.RUNNING), "Alert ingested"


async def planner_node(ctx: AgentContext, state: AgentState) -> tuple[AgentState, str]:
    state = await PlannerAgent().run(ctx, state)
    return state, f"Planner produced {len(state.subtasks)} subtasks"


async def log_analyzer_node(ctx: AgentContext, state: AgentState) -> tuple[AgentState, str]:
    state = await LogAnalyzerAgent().run(ctx, state)
    hypothesis = state.root_cause.hypothesis if state.root_cause else "no root cause"
    return state, f"Root cause: {hypothesis[:100]}"


async def code_fixer_node(ctx: AgentContext, state: AgentState) -> tuple[AgentState, str]:
    state = await CodeFixerAgent().run(ctx, state)
    assert state.patch is not None
    return state, f"Patch {state.patch.patch_ref} generated ({len(state.patch.files)} file)"

    # Patch status must re-enter DRAFT for revisions so the UI highlights the change.


async def hitl_gate_node(ctx: AgentContext, state: AgentState) -> tuple[AgentState, str]:
    patch = state.patch
    if patch is None or not patch.files:
        raise GraphError("human gate reached before any patch existed")

    # Revisions after a first approval flow through without re-blocking.
    if state.hitl_approved:
        return state, "Human gate: revision auto-passes (previously approved)"

    if ctx.settings.graph_hitl_auto_approve:
        review = HITLReview(
            incident_id=state.incident_id,
            run_id=state.run_id,
            patch_ref=patch.patch_ref,
            action=ReviewAction.APPROVE,
            reason="auto-approve (GRAPH_HITL_AUTO_APPROVE=true)",
        )
        state = await _apply_review(ctx, state, review)
        return state, "Human gate: auto-approved"

    await ctx.emit(
        EventType.PENDING_APPROVAL,
        patch_ref=patch.patch_ref,
        incident_id=state.incident_id,
        run_id=state.run_id,
        summary=patch.summary,
        diff=patch.diff,
        files=[pf.path for pf in patch.files],
        action="approve|reject|modify",
    )
    state = state.replace(status=GraphStatus.PENDING_APPROVAL)
    await ctx.emit(EventType.MEMORY_SNAPSHOT, state=state.to_dict())

    await ctx.approval_registry.prepare(state.incident_id)
    timeout_s = float(ctx.settings.graph_hitl_timeout_seconds)
    try:
        gate_review = await ctx.approval_registry.wait_for_review(state.incident_id, timeout_s)
    except asyncio.CancelledError:
        await ctx.emit(
            EventType.REVIEW_DECIDED,
            action=ReviewAction.REJECT.value,
            patch_ref=patch.patch_ref,
            reason="run interrupted by operator",
        )
        raise
    if gate_review is None:
        await ctx.emit(
            EventType.REVIEW_DECIDED,
            action=ReviewAction.REJECT.value,
            patch_ref=patch.patch_ref,
            reason="approval timeout",
        )
        timed_out = dc_replace(patch, status=PatchStatus.REJECTED, review_reason="approval timeout")
        return state.replace(patch=timed_out).finish(GraphStatus.CANCELLED), "Human gate: timed out"

    state = await _apply_review(ctx, state, gate_review)
    return state, f"Human gate: {gate_review.action.value}"


async def verifier_node(ctx: AgentContext, state: AgentState) -> tuple[AgentState, str]:
    state = await VerifierAgent().run(ctx, state)
    if state.verification is not None and state.verification.ok:
        return state, f"Verification passed (attempt {state.verifier_attempt})"
    return state, f"Verification failed (attempt {state.verifier_attempt}) — feeding fixer"


async def deploy_node(ctx: AgentContext, state: AgentState) -> tuple[AgentState, str]:
    state = await DeployAgent().run(ctx, state)
    return state, "Patch verified and applied"


async def _apply_review(ctx: AgentContext, state: AgentState, review: HITLReview) -> AgentState:
    patch = state.patch
    assert patch is not None

    if review.action is ReviewAction.APPROVE:
        approved = dc_replace(patch, status=PatchStatus.APPROVED, review_reason=review.reason)
        await ctx.emit(
            EventType.REVIEW_DECIDED,
            action=ReviewAction.APPROVE.value,
            patch_ref=patch.patch_ref,
            reason=review.reason,
        )
        await ctx.emit_log(f"[hitl] approved by operator: {review.reason or 'no comment'}")
        return state.replace(patch=approved, hitl_approved=True, status=GraphStatus.RUNNING)

    if review.action is ReviewAction.REJECT:
        rejected = dc_replace(patch, status=PatchStatus.REJECTED, review_reason=review.reason)
        await ctx.emit(
            EventType.REVIEW_DECIDED,
            action=ReviewAction.REJECT.value,
            patch_ref=patch.patch_ref,
            reason=review.reason,
        )
        await ctx.emit_log(f"[hitl] rejected by operator: {review.reason or 'no comment'}")
        return state.replace(patch=rejected, hitl_approved=True).finish(GraphStatus.CANCELLED)

    # MODIFY: merge human edits into the patch before verification.
    merged = _merge_review_files(patch, review.modified_files)
    await ctx.emit(
        EventType.REVIEW_DECIDED,
        action=ReviewAction.MODIFY.value,
        patch_ref=patch.patch_ref,
        reason=review.reason,
        modified_files=list(review.modified_files),
    )
    await ctx.emit_log(f"[hitl] operator modified {len(review.modified_files)} file(s)")
    return state.replace(patch=merged, hitl_approved=True, status=GraphStatus.RUNNING)


def _merge_review_files(patch: Patch, modifications: dict[str, str]) -> Patch:
    current = {pf.path: pf for pf in patch.files}
    new_files: list[PatchFile] = []
    for path, pf in current.items():
        new_content = modifications.get(path, pf.new_content)
        new_files.append(
            PatchFile(
                path=pf.path,
                old_content=pf.old_content,
                new_content=new_content if new_content is not None else pf.new_content,
            )
        )
    for path, content in modifications.items():
        if path not in current:
            new_files.append(PatchFile(path=path, old_content="", new_content=content or ""))
    return dc_replace(
        patch,
        files=tuple(new_files),
        status=PatchStatus.MODIFIED,
        summary=f"{patch.summary} (operator-modified)",
    )
