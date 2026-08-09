"""Shared test doubles: deterministic LLM + incident fixtures."""
from __future__ import annotations

from collections import deque

from app.llm.base import LLMClient, LLMResponse

GOOD_SERVICE = 'def ping(timeout=1):\n    return "pong"\n'
BROKEN_SERVICE = 'def ping(timeout=1):\n    raise TimeoutError("boom")\n'

INCIDENT = {
    "id": "inc_test",
    "title": "p99 latency spike in checkout",
    "severity": "high",
    "service": "checkout",
    "error": "TimeoutError",
    "target_file": "service.py",
    "files": {
        "service.py": BROKEN_SERVICE,
        "test_service.py": 'from service import ping\n\n\ndef test_ping():\n    assert ping() == "pong"\n',
    },
}


class FakeLLM(LLMClient):
    """Deterministic LLM keyed on the system prompt, with a scripted fix queue."""

    def __init__(self, fix_contents: deque[str] | None = None) -> None:
        self.fix_contents: deque[str] = fix_contents or deque([GOOD_SERVICE])
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> LLMResponse:
        self.calls.append((system, user))
        if "senior SRE planner" in system:
            return LLMResponse(
                '{"subtasks": ['
                '{"title": "inspect checkout logs", "agent": "log_analyzer", "detail": "trace p99"}, '
                '{"title": "patch timeout logic", "agent": "code_fixer", "detail": "fix retry"}, '
                '{"title": "verify with pytest", "agent": "verifier", "detail": "green build"}]}',
                input_tokens=50,
                output_tokens=24,
            )
        if "expert SRE log analyst" in system:
            return LLMResponse(
                '{"hypothesis": "checkout retry loop exceeds timeout budget", "confidence": 0.85}',
                input_tokens=60,
                output_tokens=16,
            )
        if "staff engineer fixing a production incident" in system:
            content = self.fix_contents.popleft() if self.fix_contents else GOOD_SERVICE
            return LLMResponse(content, input_tokens=80, output_tokens=32)
        return LLMResponse("ok", input_tokens=5, output_tokens=2)
