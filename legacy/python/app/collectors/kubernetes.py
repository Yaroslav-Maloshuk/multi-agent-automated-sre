"""Kubernetes collector.

* Webhook: parses a Kubernetes `Event` object (forwarded by a webhook, e.g.
  event router / alertmanager kube events).
* Poller: lists cluster events via the official `kubernetes` client and emits
  `Warning` events. Uses in-cluster config when available, otherwise
  `~/.kube/config` (or `KUBERNETES_KUBECONFIG`). Enabled when `kubernetes` is
  listed in `COLLECTORS_ENABLED`.
"""
from __future__ import annotations

import logging
from typing import Any

from app.collectors.base import AlertCollector, NormalizedAlert, normalize_severity
from app.core.config import Settings

logger = logging.getLogger(__name__)


class KubernetesCollector(AlertCollector):
    name = "kubernetes"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return True  # gated by COLLECTORS_ENABLED list, like the others

    # ── Webhook ─────────────────────────────────────────────────────────

    def from_webhook(self, payload: dict[str, Any]) -> list[NormalizedAlert]:
        # A Kubernetes Event (v1 / events.k8s.io).
        reason = str(payload.get("reason") or "KubernetesEvent")
        involved = payload.get("involvedObject") or {}
        namespace = str(payload.get("namespace") or involved.get("namespace") or "default")
        message = str(payload.get("message") or "")
        event_type = str(payload.get("type") or "Warning")

        if event_type.lower() == "normal":
            return []

        title = f"{namespace}/{reason}"
        return [
            NormalizedAlert(
                source=self.name,
                title=title[:300],
                severity=normalize_severity(event_type, fallback="high"),
                service=namespace,
                error=reason,
                message=message[:4000],
                raw=payload,
            )
        ]

    # ── Poller ──────────────────────────────────────────────────────────

    async def check(self) -> list[NormalizedAlert]:
        try:
            import kubernetes as k8s
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            logger.warning("kubernetes collector requires `pip install kubernetes`")
            raise RuntimeError("kubernetes client not installed") from exc

        return await _poll_events(k8s, self._settings)

    async def _poll(self, k8s: Any, settings: Settings) -> list[NormalizedAlert]:
        return await _poll_events(k8s, settings)


async def _poll_events(k8s: Any, settings: Settings) -> list[NormalizedAlert]:
    _load_config(k8s, settings)
    core = k8s.client.CoreV1Api()
    field_selector = "type=Warning"

    try:
        events = core.list_event_for_all_namespaces(field_selector=field_selector, limit=50)
    except Exception as exc:  # noqa: BLE001
        logger.warning("kubernetes event poll failed: %s", exc)
        return []

    collector_name = KubernetesCollector.name
    alerts: list[NormalizedAlert] = []
    for event in getattr(events, "items", []) or []:
        involved = getattr(event, "involved_object", None) or {}
        namespace = (getattr(event, "namespace", None) or "").strip() or (involved.get("namespace") or "default")
        message = getattr(event, "message", "") or ""
        reason = getattr(event, "reason", None) or "KubernetesEvent"
        title = f"{namespace}/{reason}"
        raw = {"reason": reason, "message": message, "namespace": namespace, "type": "Warning"}
        alerts.append(
            NormalizedAlert(
                source=collector_name,
                title=title[:300],
                severity=normalize_severity("Warning", fallback="high"),
                service=namespace or None,
                error=reason,
                message=str(message)[:4000],
                raw=raw,
            )
        )
    logger.info("kubernetes poll: %d warning event(s)", len(alerts))
    return alerts


def _load_config(k8s: Any, settings: Settings) -> None:
    try:
        k8s.config.load_incluster_config()
        return
    except Exception:  # noqa: BLE001
        pass
    if settings.kubernetes_kubeconfig:
        k8s.config.load_kube_config(config_file=settings.kubernetes_kubeconfig)
    else:
        k8s.config.load_kube_config()
