"""Native alert collector webhooks: normalize foreign payloads into incidents."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import FakeLLM


@pytest.fixture
def client(settings) -> TestClient:
    app = create_app(settings, llm=FakeLLM())
    with TestClient(app) as test_client:
        yield test_client


def test_prometheus_alertmanager_webhook(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/collectors/prometheus",
        json={
            "status": "firing",
            "alerts": [
                {
                    "labels": {"alertname": "HighCPU", "severity": "critical", "service": "api"},
                    "annotations": {"summary": "API CPU > 90%"},
                }
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1
    incidents = client.get("/api/v1/incidents").json()["incidents"]
    created = [i for i in incidents if i["title"] == "HighCPU"]
    assert created and created[0]["severity"] == "critical"


def test_datadog_webhook(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/collectors/datadog",
        json={"title": "Disk full on host-1", "priority": "P2", "tags": ["service:db"], "text": "/ is 95% full"},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1


def test_kubernetes_event_webhook(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/collectors/kubernetes",
        json={
            "type": "Warning",
            "reason": "BackOff",
            "namespace": "payments",
            "involvedObject": {"namespace": "payments", "name": "payments-7f9b"},
            "message": "Back-off restarting failed container",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1


def test_kubernetes_normal_events_are_ignored(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/collectors/kubernetes",
        json={"type": "Normal", "reason": "Started", "namespace": "payments"},
    )
    assert resp.json()["created"] == 0


def test_collectors_status(client: TestClient) -> None:
    resp = client.get("/api/v1/collectors")
    assert resp.status_code == 200
    assert "collectors" in resp.json()


def test_collector_webhook_dedupe_runner(client: TestClient) -> None:
    """The runner-level dedupe window suppresses repeated identical alerts."""
    from app.collectors.base import NormalizedAlert
    from app.collectors.runner import CollectorRunner

    runner = CollectorRunner(client.app.state.settings, client.app.state.incident_service.create)
    alert = NormalizedAlert(source="prometheus", title="CPU spike", severity="high")
    assert runner._dedupe(alert) is True
    assert runner._dedupe(alert) is False
