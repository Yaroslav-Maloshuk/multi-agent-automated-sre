"""REST gateway surface (TestClient, full lifespan)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import FakeLLM, INCIDENT

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}


@pytest.fixture
def client(settings) -> TestClient:
    app = create_app(settings, llm=FakeLLM())
    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["dependencies"]["bus"] == "ok"


def test_incident_full_lifecycle(client: TestClient) -> None:
    created = client.post("/api/v1/incidents", json=INCIDENT)
    assert created.status_code == 201
    incident_id = created.json()["incident_id"]

    assert client.post(f"/api/v1/incidents/{incident_id}/run").status_code == 202

    state = _poll_terminal(client, incident_id)
    assert state["status"] == "COMPLETED"
    assert state["patch"]["status"] == "APPLIED"
    assert state["root_cause"] is not None
    assert len(state["timeline"]) == 7


def test_agent_catalog(client: TestClient) -> None:
    resp = client.get("/api/v1/agents")
    assert resp.status_code == 200
    names = {agent["name"] for agent in resp.json()["agents"]}
    assert {"planner", "log_analyzer", "code_fixer", "verifier", "deploy"} <= names


def test_roi_endpoint(client: TestClient) -> None:
    created = client.post("/api/v1/incidents", json=INCIDENT)
    incident_id = created.json()["incident_id"]
    client.post(f"/api/v1/incidents/{incident_id}/run")
    _poll_terminal(client, incident_id)

    summary = client.get("/api/v1/metrics/roi").json()["summary"]
    assert summary["resolved"] >= 1
    assert summary["total_saved_amount"] > 0


def test_404_for_unknown_incident(client: TestClient) -> None:
    resp = client.get("/api/v1/incidents/nope")
    assert resp.status_code == 404


def _poll_terminal(client: TestClient, incident_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/api/v1/incidents/{incident_id}").json()["state"]
        if state["status"] in TERMINAL:
            return state
        time.sleep(0.05)
    raise AssertionError("run did not reach a terminal state in time")
