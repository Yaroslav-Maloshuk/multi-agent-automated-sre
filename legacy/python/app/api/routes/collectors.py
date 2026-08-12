"""Native alert ingestion endpoints.

Push-based collectors: Datadog event webhooks, Alertmanager notifications and
Kubernetes Event objects can be POSTed here to become incidents. The pollers
(Datadog monitors, Prometheus ALERTS, K8s events) run via `CollectorRunner`.
"""
from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_incident_service
from app.collectors.datadog import DatadogCollector
from app.collectors.kubernetes import KubernetesCollector
from app.collectors.prometheus import PrometheusCollector
from app.core.config import Settings
from app.services.incident_service import IncidentService

router = APIRouter()


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


@router.post("/collectors/datadog")
async def datadog_webhook(
    payload: dict[str, Any],
    request: Request,
    service: IncidentService = Depends(get_incident_service),
) -> dict[str, Any]:
    alerts = DatadogCollector(_settings(request)).from_webhook(payload)
    created = await _create_all(service, alerts)
    return {"source": "datadog", "received": len(alerts), "created": created}


@router.post("/collectors/prometheus")
async def prometheus_webhook(
    payload: dict[str, Any],
    request: Request,
    service: IncidentService = Depends(get_incident_service),
) -> dict[str, Any]:
    alerts = PrometheusCollector(_settings(request)).from_webhook(payload)
    created = await _create_all(service, alerts)
    return {"source": "prometheus", "received": len(alerts), "created": created}


@router.post("/collectors/kubernetes")
async def kubernetes_webhook(
    payload: dict[str, Any],
    request: Request,
    service: IncidentService = Depends(get_incident_service),
) -> dict[str, Any]:
    collector = KubernetesCollector(_settings(request))
    alerts: list[Any] = []
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                alerts.extend(collector.from_webhook(item))
    else:
        alerts = collector.from_webhook(payload)
    created = await _create_all(service, alerts)
    return {"source": "kubernetes", "received": len(alerts), "created": created}


@router.get("/collectors")
async def collectors_status(request: Request) -> dict[str, Any]:
    runner = request.app.state.collector_runner
    return {"collectors": runner.summary() if runner else [], "running": bool(runner and runner.active)}


async def _create_all(service: IncidentService, alerts: list[Any]) -> int:
    created = 0
    for alert in alerts:
        await service.create(alert.to_incident())
        created += 1
    return created
