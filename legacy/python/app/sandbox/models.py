"""Sandbox handle model shared by all sandbox backends.

`SandboxHandle` is the only interface agents touch: read/write files and run
commands. Backends (`LocalSandboxPool`, `DockerSandboxPool`) implement it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.core.errors import SandboxError


@dataclass(slots=True)
class SandboxExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(slots=True)
class SandboxHandle:
    """A live, isolated execution workspace acquired from the pool."""

    id: str
    backend: str
    container_id: str | None
    workdir: str
    incident_id: str | None = None
    acquired_at: float = field(default_factory=time.time)

    def read_file(self, path: str) -> str:
        raise NotImplementedError

    def write_file(self, path: str, content: str) -> None:
        raise NotImplementedError

    def run(self, command: str, *, cwd: str | None = None, timeout: int = 120) -> SandboxExecResult:
        raise NotImplementedError

    def execute(self, code: str, language: str = "python") -> SandboxExecResult:
        """Run arbitrary code **inside the sandbox only** — never the host."""
        if language not in {"python", "sh"}:
            raise SandboxError(f"unsupported sandbox language: {language}")
        return self.run(f"python3 - <<'PYEOF'\n{code}\nPYEOF" if language == "python" else code)

    def destroy(self) -> None:
        raise NotImplementedError

    def full_path(self, relative: str) -> str:
        return f"{self.workdir.rstrip('/')}/{relative.lstrip('/')}"
