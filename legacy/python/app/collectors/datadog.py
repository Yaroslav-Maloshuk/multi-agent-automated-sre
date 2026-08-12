"""Datadog collector.

* Webhook: parses Datadog's event-webhook POST payloads.
* Poller: lists monitors via the Datadog API v1 and emits those in
  `overall_state == Alert`. Enabled when `DATADOG_API_KEY` + `DATADOG_APP_KEY`
  are configured and `datadog` is listed in `COLLECTORS_ENABLED`.
"""
from __future__ import annotations

import logging
from typing import Any

from app.collectors.base import AlertCollector, NormalizedAlert, normalize_severity
from app.core.config import Settings

logger = logging.getLogger(__name__)


class DatadogCollector(AlertCollector):
    name = "datadog"

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return self._settings.datadog_api_key is not None and self._settings.datadog_app_key is not None

    # ── Webhook ─────────────────────────────────────────────────────────

    def from_webhook(self, payload: dict[str, Any]) -> list[NormalizedAlert]:
        # Datadog event webhook: {"title", "alert_type", "priority", "tags",
        #   "text", "host", "monitor", "link", ...}
        title = str(payload.get("title") or payload.get("event_title") or "Datadog alert")
        severity = normalize_severity(
            str(payload.get("priority") or payload.get("alert_type") or ""), fallback="high"
        )
        message = str(payload.get("text") or payload.get("body") or "")
        service = _first_tag_value(payload.get("tags", []), "service")
        return [
            NormalizedAlert(
                source=self.name,
                title=title,
                severity=severity,
                service=service,
                error=payload.get("alert_type"),
                message=message,
                raw=payload,
            )
        ]

    # ── Poller ──────────────────────────────────────────────────────────

    async def check(self) -> list[NormalizedAlert]:
        api_key = self._settings.datadog_api_key or ""
        app_key = self._settings.datadog_app_key or ""
        base = f"https://api.{self._settings.datadog_site}"
        tags = [t for t in self._settings.datadog_monitor_tags.split(",") if t]

        async with _httpx() as client:
            resp = await client.get(
                f"{base}/api/v1/monitor",
                params={"tags": ",".join(tags)} if tags else None,
                headers={"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key},
                timeout=20,
            )
        if resp.status_code != 200:
            logger.warning("datadog monitor poll failed: %s %s", resp.status_code, resp.text[:300])
            return []
        if resp.headers.get("content-type", "").startswith("application/json"):
            monitors = resp.json()
        else:
            monitors = resp.text

        alerts: list[NormalizedAlert] = []
        for monitor in _iter_list(monitors):
            if monitor.get("overall_state") != "Alert":
                continue
            name = str(monitor.get("name") or monitor.get("query") or "Datadog monitor")
            priority = str(monitor.get("priority") or monitor.get("priority_str") or "p3")
            alerts.append(
                NormalizedAlert(
                    source=self.name,
                    title=name[:300],
                    severity=normalize_severity(priority),
                    service=_first_tag_value(monitor.get("tags", []), "service"),
                    error=monitor.get("type"),
                    message=str(monitor.get("message") or "")[:4000],
                    raw=dict(monitor),
                )
            )
        logger.info("datadog poll: %d firing monitor(s)", len(alerts))
        return alerts


def _first_tag_value(tags: Any, key: str) -> str | None:
    if not tags:
        return None
    for tag in tags:
        if isinstance(tag, str) and tag.lower().startswith(f"{key}:"):
            return tag.split(":", 1)[1]
    return None


def _iter_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict) and isinstance(value.get("monitors"), list):
        return [v for v in value["monitors"] if isinstance(v, dict)]
    return []


def _httpx() -> Any:
    import httpx

    return httpx.AsyncClient
