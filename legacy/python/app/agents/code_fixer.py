"""Code Fixer agent: produces a real, diff-able source patch.

The fix is generated inside an isolated sandbox against the incident's file
snapshot, streamed to the UI as live "typing" telemetry, and returned as a
unified Git-style diff. It never touches the host filesystem.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import re
from uuid import uuid4

from app.agents.base import BaseAgent
from app.bus.schemas import EventType
from app.core.errors import GraphError, LLMError
from app.graph.context import AgentContext
from app.graph.state import AgentName, AgentState, Patch, PatchFile, PatchStatus

_SYSTEM_PROMPT = (
    "You are a staff engineer fixing a production incident. You are given a "
    "source file and a root-cause hypothesis. Rewrite the file to eliminate "
    "the root cause WITHOUT changing its public interface. Preserve all "
    "existing tests' expectations. If verification feedback is provided, "
    "address it precisely. Respond with the COMPLETE new file contents only, "
    "no commentary, no markdown fences."
)


class CodeFixerAgent(BaseAgent):
    name = AgentName.CODE_FIXER

    async def run(self, ctx: AgentContext, state: AgentState) -> AgentState:
        patch, state = await self._propose_patch(ctx, state)
        state = state.replace(patch=patch)
        await ctx.emit_log(f"[code_fixer] patch {patch.patch_ref} ready ({len(patch.files)} file(s))")
        return state

    async def _propose_patch(self, ctx: AgentContext, state: AgentState) -> tuple[Patch, AgentState]:
        incident = state.incident
        files = incident.get("files")
        if not isinstance(files, dict) or not files:
            raise GraphError(
                "code_fixer requires an incident `files` snapshot",
                details={"hint": "attach repo files to the incident payload"},
            )
        target = str(incident.get("target_file") or next(iter(files)))
        old_content = files.get(target)
        if old_content is None:
            raise GraphError(f"target file not present in incident snapshot: {target}")

        root_cause = state.root_cause.hypothesis if state.root_cause else "unknown"
        feedback = state.verifier_feedback or "no previous attempt"

        handle = await ctx.sandbox_pool.acquire(incident)
        try:
            await asyncio.to_thread(handle.write_file, target, old_content)
            await ctx.emit(
                EventType.SANDBOX_ACQUIRED,
                sandbox_id=handle.id,
                backend=handle.backend,
                target_file=target,
            )
            await ctx.emit_log(f"[code_fixer] sandbox {handle.id} acquired; fixing {target}")

            try:
                new_content, state = await self.complete(
                    ctx,
                    state,
                    _SYSTEM_PROMPT,
                    json.dumps(
                        {
                            "target_file": target,
                            "file_content": old_content,
                            "root_cause": root_cause,
                            "verifier_feedback": feedback,
                        },
                        ensure_ascii=False,
                    ),
                )
                new_content = _strip_fences(new_content)
            except LLMError as exc:
                await ctx.emit_log(f"[code_fixer] llm unavailable, using rule-based fix ({exc})", level="warn")
                new_content = _rule_based_fix(target, old_content, root_cause)
            if not new_content.strip():
                raise LLMError("fixer returned an empty file", details={"target": target})

            await self._stream_typing(ctx, new_content)
            diff = _unified_diff(target, old_content, new_content)
            patch = Patch(
                patch_ref=f"patch_{uuid4().hex[:16]}",
                files=(PatchFile(path=target, old_content=old_content, new_content=new_content),),
                diff=diff,
                summary=f"Fix for {target} — {root_cause[:120]}",
                status=PatchStatus.DRAFT,
            )

            await ctx.emit(EventType.PATCH_DIFF, patch_ref=patch.patch_ref, diff=diff, files=[target])
            await ctx.emit(
                EventType.PATCH_GENERATED,
                patch_ref=patch.patch_ref,
                summary=patch.summary,
                files=[{"path": target, "added": _added_lines(diff), "removed": _removed_lines(diff)}],
            )
            await asyncio.to_thread(handle.write_file, target, new_content)
            return patch, state
        finally:
            await ctx.sandbox_pool.release(handle)
            await ctx.emit(
                EventType.SANDBOX_RELEASED,
                sandbox_id=handle.id,
                backend=handle.backend,
            )

    async def _stream_typing(self, ctx: AgentContext, content: str) -> None:
        step = max(1, len(content) // 40)
        for index in range(0, len(content), step):
            chunk = content[index : index + step]
            await ctx.emit(
                EventType.PATCH_TYPING,
                cursor_offset=index,
                chunk=chunk,
            )
            await asyncio.sleep(0.004)


def _strip_fences(content: str) -> str:
    lines = content.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).rstrip() + ("\n" if content.endswith("\n") else "")


def _unified_diff(target: str, old_content: str, new_content: str) -> str:
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{target}",
            tofile=f"b/{target}",
            lineterm="",
        )
    )


def _added_lines(diff: str) -> int:
    return sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))


def _removed_lines(diff: str) -> int:
    return sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))


# Deterministic, syntax-safe remediation rules used when no LLM is available.
# Mirrors the graceful degradation already present in Planner and Log Analyzer
# so the engine never stalls on model availability.
_RULE_BASED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(redis\.Redis\.from_url\([^)]*?)\s*\)", r"\1, socket_connect_timeout=3, socket_timeout=5)"),
    (r"(redis\.Redis\([^)]*?)\s*\)", r"\1, socket_connect_timeout=3, socket_timeout=5)"),
    (r"(requests\.(?:get|post|put|patch|delete)\([^,)]+)([^)]*)\)", r"\1\2, timeout=10)"),
    (r"(sqlite3\.connect\([^,)]+)([^)]*)\)", r"\1\2, timeout=5)"),
)


def _rule_based_fix(target: str, old_content: str, root_cause: str) -> str:
    content = old_content
    changed = False
    for pattern, repl in _RULE_BASED_PATTERNS:
        content, count = re.subn(pattern, repl, content)
        changed = changed or count > 0
    if not changed:
        content = _annotate_fix(content, target, root_cause)
    return content


def _annotate_fix(content: str, target: str, root_cause: str) -> str:
    banner = (
        f"# Auto-SRE remediation for {target}\n"
        f"# root cause: {root_cause}\n"
        "# no matching remediation rule applied; behavior unchanged\n"
    )
    return banner + ("" if content.startswith("\n") else "\n") + content
