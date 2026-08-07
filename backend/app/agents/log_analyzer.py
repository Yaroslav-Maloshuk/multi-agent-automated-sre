"""Log Analyzer agent: hybrid retrieval (pgvector + BM25) → root cause.

Distills multi-gigabyte log corpora into a concise, evidence-backed
`RootCauseSummary` and indexes the result back into long-term memory so future
incidents resolve faster.
"""
from __future__ import annotations

import json
from typing import Any

from app.agents.base import BaseAgent
from app.bus.schemas import EventType
from app.core.errors import LLMError
from app.graph.context import AgentContext
from app.graph.state import AgentName, AgentState, RootCauseSummary
from app.memory.hybrid import MemoryDocument, build_embedder

_SYSTEM_PROMPT = (
    "You are an expert SRE log analyst. Based ONLY on the evidence below, state "
    "the most probable root cause of the incident. Respond ONLY with JSON:\n"
    '{"hypothesis": str, "confidence": float}\n'
    "Keep the hypothesis under 120 words."
)


class LogAnalyzerAgent(BaseAgent):
    name = AgentName.LOG_ANALYZER

    async def run(self, ctx: AgentContext, state: AgentState) -> AgentState:
        incident = state.incident
        query = self._build_query(incident)
        await ctx.emit_log(f"[log_analyzer] hybrid search: {query}")

        hits = await ctx.memory.hybrid_search(query, top_k=5)
        evidence = [hit.text for hit in hits if hit.text.strip()]
        await ctx.emit_log(f"[log_analyzer] retrieved {len(evidence)} evidence spans")

        for hit in hits[:3]:
            await ctx.emit(
                EventType.AGENT_STEP,
                agent=self.name.value,
                step={"title": "evidence span", "detail": hit.text[:400], "memory_id": hit.id},
            )

        summary = await self._summarize(ctx, state, incident, evidence, [hit.id for hit in hits])
        await ctx.emit_log(
            f"[log_analyzer] root cause (conf={summary.confidence:.2f}): {summary.hypothesis[:200]}"
        )
        await ctx.emit(
            EventType.AGENT_STEP,
            agent=self.name.value,
            step={"title": "root cause", "detail": summary.hypothesis, "confidence": summary.confidence},
        )

        state = state.replace(root_cause=summary)
        await self._persist(ctx, state, summary)
        return state

    def _build_query(self, incident: dict[str, Any]) -> str:
        parts = [
            str(incident.get("title", "")),
            str(incident.get("error", incident.get("message", ""))),
            str(incident.get("service", "")),
        ]
        return " ".join(p for p in parts if p).strip() or "production incident"

    async def _summarize(
        self,
        ctx: AgentContext,
        state: AgentState,
        incident: dict[str, Any],
        evidence: list[str],
        memory_refs: list[str],
    ) -> RootCauseSummary:
        if evidence:
            try:
                data, state = await self.complete_json(
                    ctx,
                    state,
                    _SYSTEM_PROMPT,
                    json.dumps(
                        {"incident": incident, "evidence": evidence[:10]},
                        ensure_ascii=False,
                        default=str,
                    ),
                )
                confidence = float(data.get("confidence", 0.6))
                return RootCauseSummary(
                    hypothesis=str(data.get("hypothesis", "")).strip(),
                    evidence=evidence[:5],
                    confidence=max(0.0, min(1.0, confidence)),
                    memory_refs=memory_refs,
                )
            except (LLMError, ValueError):
                await ctx.emit_log("[log_analyzer] llm degraded, using evidence-based summary", level="warn")

        first = evidence[0][:400] if evidence else "no correlated evidence found"
        return RootCauseSummary(
            hypothesis=f"Top evidence span suggests: {first}",
            evidence=evidence[:5],
            confidence=0.35 if evidence else 0.1,
            memory_refs=memory_refs,
        )

    async def _persist(self, ctx: AgentContext, state: AgentState, summary: RootCauseSummary) -> None:
        try:
            embedder = build_embedder(ctx.settings)
            embedding = await embedder.embed(summary.hypothesis)
            await ctx.memory.index_document(
                MemoryDocument(
                    doc_id=f"rc:{state.run_id}",
                    text=summary.hypothesis,
                    metadata={
                        "incident_id": state.incident_id,
                        "run_id": state.run_id,
                        "kind": "root_cause",
                    },
                ),
                embedding=embedding,
            )
        except Exception as exc:  # noqa: BLE001 — memory persistence is best-effort
            await ctx.emit_log(f"[log_analyzer] failed to persist summary ({exc})", level="warn")
