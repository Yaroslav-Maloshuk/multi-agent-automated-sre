"""End-to-end WebSocket contract test: streaming + HITL review over the wire."""
from __future__ import annotations

import concurrent.futures
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import FakeLLM, INCIDENT


@pytest.fixture
def ws_client(settings) -> TestClient:
    settings = settings.model_copy(update={"graph_hitl_auto_approve": False})
    app = create_app(settings, llm=FakeLLM())
    with TestClient(app) as test_client:
        yield test_client


def test_ws_streams_pending_approval_and_hitl_review(ws_client: TestClient) -> None:
    created = ws_client.post("/api/v1/incidents", json=INCIDENT)
    incident_id = created.json()["incident_id"]

    with ws_client.websocket_connect(f"/ws/stream?channels=incident:{incident_id}") as ws:
        assert _recv_type(ws) == "connected"
        assert _recv_type(ws) == "subscribed"

        assert ws_client.post(f"/api/v1/incidents/{incident_id}/run").status_code == 202

        pending = _recv_until_type(ws, "hitl.pending_approval")
        patch_ref = pending["payload"]["patch_ref"]

        ws.send_json(
            {
                "action": "hitl.review",
                "incident_id": incident_id,
                "payload": {"action": "approve", "reason": "ws approved", "patch_ref": patch_ref},
            }
        )
        assert _recv_type(ws) == "review.accepted"

        state = _poll_terminal(ws_client, incident_id)
        assert state["status"] == "COMPLETED"
        assert state["patch"]["status"] == "APPLIED"


def _recv_type(ws) -> str:
    return _recv(ws)["type"]


def _recv_until_type(ws, expected: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = _recv(ws, timeout=max(0.1, deadline - time.monotonic()))
        if data.get("type") == expected:
            return data
    raise AssertionError(f"did not receive event type {expected} in time")


def _recv(ws, timeout: float = 10.0) -> dict:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(ws.receive_json)
        return future.result(timeout=timeout)


def _poll_terminal(client: TestClient, incident_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/api/v1/incidents/{incident_id}").json()["state"]
        if state["status"] in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}:
            return state
        time.sleep(0.05)
    raise AssertionError("run did not reach a terminal state in time")
