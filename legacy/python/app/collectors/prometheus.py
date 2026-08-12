"""Prometheus / Alertmanager collector.

* Webhook: parses Alertmanager `alert` notification payloads.
* Poller: runs the `ALERTS` metric query against the Prometheus HTTP API and
  emits every `alertstate == firing` series. Enabled when `PROMETHEUS_URL` is
  configured and `prometheus` is listed in `COLLECTORS_ENABLED`.
"""
from __future__ import annotations

import logging
from typing import Any

from app.collectors.base import AlertCollector, NormalizedAlert, normalize_severity
from app.core.config import Settings

logger = logging.getLogger(__name__)


class PrometheusCollector(AlertCollector):
    name = "prometheus"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self._settings.prometheus_url)

    # ── Webhook ─────────────────────────────────────────────────────────

    def from_webhook(self, payload: dict[str, Any]) -> list[NormalizedAlert]:
        # Alertmanager: {"status": "firing", "alerts": [{"labels", "annotations", "startsAt"}], ...}
        alerts: list[NormalizedAlert] = []
        for alert in payload.get("alerts", []):
            if not isinstance(alert, dict):
                continue
            labels = _as_dict(alert.get("labels"))
            annotations = _as_dict(alert.get("annotations"))
            name = str(
                labels.get("alertname")
                or labels.get("alert")
                or annotations.get("summary")
                or annotations.get("title")
                or "Prometheus alert"
            )
            alerts.append(
                NormalizedAlert(
                    source=self.name,
                    title=name[:300],
                    severity=normalize_severity(labels.get("severity")),
                    service=labels.get("service") or labels.get("job") or labels.get("namespace"),
                    error=labels.get("alertname"),
                    message=str(annotations.get("description") or annotations.get("summary") or "")[:4000],
                    raw=alert,
                )
            )
        return alerts

    # ── Poller ──────────────────────────────────────────────────────────

    async def check(self) -> list[NormalizedAlert]:
        import httpx

        base = self._settings.prometheus_url.rstrip("/")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                f"{base}/api/v1/query", params={"query": self._settings.prometheus_query}
            )
        if resp.status_code != 200:
            logger.warning("prometheus poll failed: %s %s", resp.status_code, resp.text[:300])
            return []
        result = resp.json().get("data", {}).get("result", [])
        if not isinstance(result, list):
            return []

        alerts: list[NormalizedAlert] = []
        for series in result:
            if not isinstance(series, dict):
                continue
            metric = _as_dict(series.get("metric"))
            if metric.get("alertstate") not in (None, "firing"):
                continue
            value = series.get("value")
            raw = {**metric, "_value": value}
            name = str(metric.get("alertname") or "Prometheus alert")
            alerts.append(
                NormalizedAlert(
                    source=self.name,
                    title=name[:300],
                    severity=normalize_severity(metric.get("severity")),
                    service=metric.get("service") or metric.get("job") or metric.get("namespace"),
                    error=metric.get("alertname"),
                    raw=raw,
                )
            )
        logger.info("prometheus poll: %d firing alert(s)", len(alerts))
        return alerts


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
