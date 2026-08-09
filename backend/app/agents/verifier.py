"""Verifier agent: runs pytest + linters inside the sandbox on the patch.

Any failure becomes structured feedback for the self-correction loop (the
executor re-queues the fixer with this feedback).
"""
from __future__ import annotations

import re
from typing import Any

from app.agents.base import BaseAgent
from app.bus.schemas import EventType
from app.core.errors import GraphError
from app.graph.context import AgentContext
from app.graph.state import AgentName, AgentState, VerificationReport


class VerifierAgent(BaseAgent):
    name = AgentName.VERIFIER

    async def run(self, ctx: AgentContext, state: AgentState) -> AgentState:
        patch = state.patch
        if patch is None or not patch.files:
            raise GraphError("verifier requires a generated patch")

        attempt = state.verifier_attempt + 1
        handle = await ctx.sandbox_pool.acquire(state.incident)
        try:
            for patch_file in patch.files:
                await asyncio_run_sync(handle.write_file, patch_file.path, patch_file.new_content)

            await ctx.emit_log(f"[verifier] running pytest (attempt {attempt})")
            test_result = await asyncio_run(handle.run, "pytest -q --tb=short", timeout_s=240)

            changed = [pf.path for pf in patch.files]
            lint_output = ""
            lint_ok = True
            if changed:
                lint = await asyncio_run(handle.run, f"flake8 {' '.join(changed)}", timeout_s=60)
                lint_output = lint.stdout + lint.stderr
                # A missing linter is a skip, not a failure.
                lint_ok = lint.ok or _linter_unavailable(lint.exit_code, lint_output)

            failures = _parse_failures(test_result.stdout)
            combined_output = _truncate(test_result.stdout + "\n---\n" + lint_output, 8_000)
            ok = test_result.ok and lint_ok

            report = VerificationReport(
                ok=ok,
                output=combined_output,
                failures=failures,
                duration_seconds=test_result.duration_seconds,
                attempt=attempt,
            )
            state = state.replace(verification=report, verifier_attempt=attempt)
            if ok:
                state = state.replace(verifier_feedback=None)
                await ctx.emit_log(f"[verifier] SUCCESS on attempt {attempt} ({report.duration_seconds:.1f}s)")
            else:
                state = state.replace(verifier_feedback=_feedback_for(report))
                await ctx.emit_log(f"[verifier] FAILED attempt {attempt}: {failures[:3]}")
            await ctx.emit(
                EventType.AGENT_STEP,
                agent=self.name.value,
                step={
                    "title": "verification",
                    "detail": f"pytest exit={test_result.exit_code} ok={ok}",
                    "failures": failures[:5],
                    "attempt": attempt,
                },
            )
            return state
        finally:
            await ctx.sandbox_pool.release(handle)


def _parse_failures(pytest_output: str) -> list[str]:
    lines = []
    for line in pytest_output.splitlines():
        if re.match(r"^(FAILED|ERROR|E\s)", line):
            lines.append(line.strip())
        if len(lines) >= 10:
            break
    return lines


def _linter_unavailable(exit_code: int, output: str) -> bool:
    if exit_code == 127:
        return True
    if exit_code != 0 and "command not found" in output:
        return True
    return False


def _feedback_for(report: VerificationReport) -> str:
    head = "\n".join(report.failures[:5]) or report.output[:400]
    return f"Verification failed (attempt {report.attempt}):\n{head}"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…(truncated)"


async def asyncio_run(fn: Any, command: str, *, timeout_s: int) -> Any:
    import asyncio

    return await asyncio.to_thread(fn, command, timeout=timeout_s)


async def asyncio_run_sync(fn: Any, *args: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(fn, *args)
