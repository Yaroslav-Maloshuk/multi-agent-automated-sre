"""Sandbox pools: pre-warmed Docker containers (production) and local
temp-dir workspaces (development / tests). Both honour the same interface so
the graph is backend-agnostic.

The warm pool keeps `min_warm` dormant containers running a no-op; acquiring
one is a sub-millisecond *take* — the zero-latency path for agent execution.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.core.errors import SandboxUnavailableError
from app.sandbox.docker_client import DockerSandboxClient
from app.sandbox.models import SandboxExecResult, SandboxHandle

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SandboxPoolStats:
    warm: int
    in_use: int
    max_size: int
    backend: str


class SandboxPool(ABC):
    """Async pool of isolated execution workspaces."""

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def warm(self, count: int | None = None) -> int:
        raise NotImplementedError

    @abstractmethod
    async def acquire(self, incident: dict[str, Any] | None = None) -> SandboxHandle:
        raise NotImplementedError

    @abstractmethod
    async def release(self, handle: SandboxHandle) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stats(self) -> SandboxPoolStats:
        raise NotImplementedError


def _write_files(root: str, files: dict[str, str]) -> None:
    for rel_path, content in files.items():
        safe = rel_path.lstrip("/")
        if ".." in safe.split("/"):
            continue
        target = Path(root) / safe
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


class LocalSandboxHandle(SandboxHandle):
    def __init__(self, workdir: str, incident_id: str | None = None) -> None:
        super().__init__(
            id=f"sbx_{uuid4().hex[:12]}",
            backend="local",
            container_id=None,
            workdir=workdir,
            incident_id=incident_id,
        )

    def read_file(self, path: str) -> str:
        full = Path(self.workdir) / path.lstrip("/")
        return full.read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        full = Path(self.workdir) / path.lstrip("/")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    def run(self, command: str, *, cwd: str | None = None, timeout: int = 120) -> SandboxExecResult:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                ["/bin/sh", "-c", command],
                cwd=cwd or self.workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_venv_aware_env(),
            )
            return SandboxExecResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_seconds=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxExecResult(
                exit_code=-1,
                stdout=exc.stdout or "",
                stderr="command timed out",
                duration_seconds=time.monotonic() - started,
            )

    def destroy(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


def _venv_aware_env() -> dict[str, str]:
    """Expose the active venv's pytest/python to the local sandbox shell."""
    import sys

    env = os.environ.copy()
    if hasattr(sys, "base_prefix") and sys.prefix != sys.base_prefix:
        bin_dir = Path(sys.prefix) / "bin"
        if bin_dir.is_dir():
            env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return env


class LocalSandboxPool(SandboxPool):
    """Development backend: an isolated temp-dir workspace per acquire.

    Commands run via `subprocess` *inside* the workspace cwd with resource
    limits — suitable for local dev and the full pytest suite.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._root: str | None = None
        self._in_use: set[str] = set()

    async def start(self) -> None:
        self._root = tempfile.mkdtemp(prefix="auto-sre-sandbox-")
        logger.info("local sandbox pool ready at %s", self._root)

    async def stop(self) -> None:
        if self._root:
            shutil.rmtree(self._root, ignore_errors=True)
            self._root = None

    async def warm(self, count: int | None = None) -> int:
        return 0

    async def acquire(self, incident: dict[str, Any] | None = None) -> SandboxHandle:
        assert self._root is not None, "pool not started"
        workdir = tempfile.mkdtemp(dir=self._root, prefix="ws-")
        incident_id = str(incident.get("id")) if incident else None
        if incident:
            files = incident.get("files")
            if isinstance(files, dict):
                _write_files(workdir, files)
        handle = LocalSandboxHandle(workdir, incident_id=incident_id)
        self._in_use.add(handle.id)
        return handle

    async def release(self, handle: SandboxHandle) -> None:
        handle.destroy()
        self._in_use.discard(handle.id)

    async def stats(self) -> SandboxPoolStats:
        return SandboxPoolStats(
            warm=0,
            in_use=len(self._in_use),
            max_size=self._settings.sandbox_pool_max,
            backend="local",
        )


class DockerSandboxHandle(SandboxHandle):
    def __init__(self, *, container_id: str, workdir: str, client: DockerSandboxClient, incident_id: str | None) -> None:
        super().__init__(
            id=f"sbx_{uuid4().hex[:12]}",
            backend="docker",
            container_id=container_id,
            workdir=workdir,
            incident_id=incident_id,
        )
        self._client = client

    def read_file(self, path: str) -> str:
        safe = _safe_rel_path(path)
        _, stdout, stderr = self._client.exec(self.container_id or "", f"cat '{self.workdir}/{safe}'")
        if stderr:
            raise FileNotFoundError(stderr.strip())
        return stdout

    def write_file(self, path: str, content: str) -> None:
        safe = _safe_rel_path(path)
        self._client.exec(
            self.container_id or "",
            f"mkdir -p '$(dirname {self.workdir}/{safe})' && cat > '{self.workdir}/{safe}'",
            stdin_data=content.encode(),
        )

    def run(self, command: str, *, cwd: str | None = None, timeout: int = 120) -> SandboxExecResult:
        started = time.monotonic()
        workdir = cwd or self.workdir
        exit_code, stdout, stderr = self._client.exec(
            self.container_id or "", f"cd '{workdir}' && {command}", timeout=timeout
        )
        return SandboxExecResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
        )

    def destroy(self) -> None:
        if self.container_id:
            self._client.remove(self.container_id)


def _safe_rel_path(path: str) -> str:
    safe = path.lstrip("/")
    if any(part == ".." for part in safe.split("/")):
        raise ValueError(f"path traversal not allowed: {path}")
    return safe


class DockerSandboxPool(SandboxPool):
    """Production pool of pre-warmed, network-isolated Docker containers."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = DockerSandboxClient(settings)
        self._warm: deque[SandboxHandle] = deque()
        self._in_use: dict[str, SandboxHandle] = {}
        self._started = False

    async def start(self) -> None:
        self._started = True
        await self.warm(self._settings.sandbox_pool_min_warm)

    async def stop(self) -> None:
        self._started = False
        for handle in list(self._warm):
            handle.destroy()
        for handle in list(self._in_use.values()):
            handle.destroy()
        self._warm.clear()
        self._in_use.clear()

    async def warm(self, count: int | None = None) -> int:
        if not self._started and count is None:
            return 0
        if not self._client.image_exists():
            await asyncio.to_thread(self._client.build_image)
        target = count if count is not None else self._settings.sandbox_pool_min_warm
        created = 0
        while len(self._warm) < target and (len(self._warm) + len(self._in_use)) < self._settings.sandbox_pool_max:
            container = await asyncio.to_thread(self._client.create_warm_container)
            handle = DockerSandboxHandle(
                container_id=container.id,
                workdir=self._settings.sandbox_default_workdir,
                client=self._client,
                incident_id=None,
            )
            self._warm.append(handle)
            created += 1
        logger.info("warm sandbox pool: %s ready (+%s new)", len(self._warm), created)
        return created

    async def acquire(self, incident: dict[str, Any] | None = None) -> SandboxHandle:
        handle = self._warm.popleft() if self._warm else await self._create()
        if not self._client.inspect_health(handle.container_id or ""):
            handle.destroy()
            handle = await self._create()
        incident_id = str(incident.get("id")) if incident else None
        handle.incident_id = incident_id
        handle.acquired_at = time.time()
        await asyncio.to_thread(self._reset_workspace, handle)
        if incident:
            files = incident.get("files")
            if isinstance(files, dict):
                for rel_path, content in files.items():
                    await asyncio.to_thread(handle.write_file, rel_path, content)
        self._in_use[handle.id] = handle
        return handle

    def _reset_workspace(self, handle: SandboxHandle) -> None:
        handle.run("rm -rf /workspace/* /workspace/.[!.]* 2>/dev/null || true")

    async def _create(self) -> SandboxHandle:
        total = len(self._warm) + len(self._in_use)
        if total >= self._settings.sandbox_pool_max:
            raise SandboxUnavailableError(
                f"sandbox pool exhausted ({total}/{self._settings.sandbox_pool_max})"
            )
        container = await asyncio.to_thread(self._client.create_warm_container)
        return DockerSandboxHandle(
            container_id=container.id,
            workdir=self._settings.sandbox_default_workdir,
            client=self._client,
            incident_id=None,
        )

    async def release(self, handle: SandboxHandle) -> None:
        self._in_use.pop(handle.id, None)
        if not self._started:
            handle.destroy()
            return
        idle = len(self._warm)
        if idle >= self._settings.sandbox_pool_min_warm:
            handle.destroy()
        else:
            handle.incident_id = None
            self._warm.append(handle)

    async def stats(self) -> SandboxPoolStats:
        return SandboxPoolStats(
            warm=len(self._warm),
            in_use=len(self._in_use),
            max_size=self._settings.sandbox_pool_max,
            backend="docker",
        )


def build_sandbox_pool(settings: Settings) -> SandboxPool:
    if settings.is_test or os.environ.get("AUTO_SRE_SANDBOX_BACKEND") == "local":
        return LocalSandboxPool(settings)
    return DockerSandboxPool(settings)
