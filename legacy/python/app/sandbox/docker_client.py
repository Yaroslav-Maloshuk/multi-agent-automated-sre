"""Low-level Docker SDK wrapper for isolated agent sandboxes.

The container image is expected to ship Python 3.11+, pytest, and linters.
Every container is created with:
* `network_mode="none"`            — no network egress (strict isolation)
* `read_only=True` rootfs          — agents cannot mutate the image
* writable named volume at /workspace
* memory + CPU caps                 — noisy-neighbor protection
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings
from app.core.errors import SandboxError

logger = logging.getLogger(__name__)

_CONTAINER_WAIT_COMMAND = ["sleep", "infinity"]


class DockerSandboxClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None

    def _get(self) -> Any:
        if self._client is None:
            import docker

            try:
                self._client = docker.from_env()
                self._client.ping()
            except Exception as exc:  # noqa: BLE001
                raise SandboxError(f"docker daemon unreachable: {exc}") from exc
        return self._client

    def image_exists(self) -> bool:
        import docker.errors

        try:
            self._get().images.get(self._settings.docker_sandbox_image)
            return True
        except docker.errors.ImageNotFound:
            return False
        except Exception as exc:  # noqa: BLE001
            raise SandboxError(f"docker image check failed: {exc}") from exc

    def build_image(self) -> None:
        build_path = self._settings.auto_sre_sandbox_image_build_path
        if not build_path:
            raise SandboxError(
                "sandbox image missing; set AUTO_SRE_SANDBOX_IMAGE_BUILD_PATH "
                "or run `make sandbox-image`"
            )
        logger.info("building sandbox image %s from %s", self._settings.docker_sandbox_image, build_path)
        image, _ = self._get().images.build(
            path=build_path,
            tag=self._settings.docker_sandbox_image,
            rm=True,
        )
        logger.info("sandbox image ready: %s", image.short_id)

    def create_warm_container(self) -> Any:
        client = self._get()
        settings = self._settings
        volume_name = _volume_name()
        client.volumes.create(name=volume_name)
        try:
            container = client.containers.run(
                image=settings.docker_sandbox_image,
                command=_CONTAINER_WAIT_COMMAND,
                detach=True,
                network_mode=settings.sandbox_network_mode,
                mem_limit=settings.sandbox_mem_limit,
                nano_cpus=int(settings.sandbox_cpu_quota * 1_000_000_000),
                read_only=True,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
                working_dir=settings.sandbox_default_workdir,
                volumes={volume_name: {"bind": settings.sandbox_default_workdir, "mode": "rw"}},
                labels={"auto-sre": "sandbox", "pooled": "true"},
            )
            return container
        except Exception as exc:  # noqa: BLE001
            raise SandboxError(f"failed to create sandbox container: {exc}") from exc

    def exec(self, container_id: str, command: str, *, timeout: int = 120) -> tuple[int, str, str]:
        container = self._get().containers.get(container_id)
        try:
            exec_id = container.client.api.exec_create(container_id, ["/bin/sh", "-c", command])["Id"]
            output = container.client.api.exec_start(exec_id, demux=True)
            exit_code = container.client.api.exec_inspect(exec_id)["ExitCode"]
            stdout, stderr = _demux(output)
            return exit_code, stdout, stderr
        except Exception as exc:  # noqa: BLE001
            raise SandboxError(f"sandbox exec failed: {exc}") from exc

    def inspect_health(self, container_id: str) -> bool:
        try:
            self._get().containers.get(container_id)
            return True
        except Exception:  # noqa: BLE001
            return False

    def remove(self, container_id: str) -> None:
        try:
            self._get().containers.get(container_id).remove(force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to remove sandbox container %s: %s", container_id, exc)


def _demux(output: Any) -> tuple[str, str]:
    if output is None:
        return "", ""
    if isinstance(output, tuple):
        stdout, stderr = output
        return (stdout or b"").decode(errors="replace"), (stderr or b"").decode(errors="replace")
    return output.decode(errors="replace"), ""


def _volume_name() -> str:
    from uuid import uuid4

    return f"auto-sre-{uuid4().hex[:12]}"
