"""Deploy agent: marks an approved, verified patch as applied.

If `deploy_webhook_url` is configured, ships the approved diff to the CD
pipeline; otherwise the applied event is the deploy hand-off.
"""
from __future__ import annotations

import httpx

from app.agents.base import BaseAgent
from app.bus.schemas import EventType
from app.core.errors import GraphError
from app.graph.context import AgentContext
from app.graph.state import AgentName, AgentState, GraphStatus, Patch, PatchStatus


class DeployAgent(BaseAgent):
    name = AgentName.DEPLOY

    async def run(self, ctx: AgentContext, state: AgentState) -> AgentState:
        patch = state.patch
        if patch is None:
            raise GraphError("deploy requires an approved, verified patch")

        applied = Patch(
            patch_ref=patch.patch_ref,
            files=patch.files,
            diff=patch.diff,
            summary=patch.summary,
            status=PatchStatus.APPLIED,
            review_reason=patch.review_reason,
        )
        state = state.replace(patch=applied)

        await ctx.emit(
            EventType.PATCH_APPLIED,
            patch_ref=applied.patch_ref,
            summary=applied.summary,
            files=[pf.path for pf in applied.files],
        )
        await ctx.emit_log(f"[deploy] patch {applied.patch_ref} applied")

        webhook = ctx.settings.deploy_webhook_url
        if webhook:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        webhook,
                        json={
                            "incident_id": state.incident_id,
                            "run_id": state.run_id,
                            "patch_ref": applied.patch_ref,
                            "diff": applied.diff,
                            "summary": applied.summary,
                        },
                    )
                await ctx.emit_log(f"[deploy] webhook -> {resp.status_code}")
            except httpx.HTTPError as exc:
                await ctx.emit_log(f"[deploy] webhook failed: {exc}", level="warn")

        return state.finish(GraphStatus.COMPLETED)
