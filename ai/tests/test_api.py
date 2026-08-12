"""HTTP-level tests for the FastAPI gateway (no Docker / weights / keys)."""


from fastapi.testclient import TestClient

from ai.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "candle" in body["providers"]


def test_providers_endpoint():
    body = client.get("/v1/providers").json()
    assert "local" in body


def test_chat_completion():
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "local/test", "messages": [{"role": "user", "content": "planner: go"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"].startswith("{")


def test_chat_stream_is_sse():
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "local/test", "stream": True, "messages": [{"role": "user", "content": "planner: go"}]},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
        assert "data: " in body
        assert "[DONE]" in body


def test_embeddings_string_and_array_input():
    for payload in ({"input": "disk full"}, {"input": ["disk full", "cpu"]}):
        resp = client.post("/v1/embeddings", json=payload)
        assert resp.status_code == 200
        dims = len(resp.json()["data"][0]["embedding"])
        assert dims == 384


def test_structured_extraction():
    resp = client.post(
        "/v1/structured",
        json={
            "model": "local/test",
            "prompt": "log_analyzer: diagnose",
            "schema": {
                "type": "object",
                "properties": {
                    "root_cause": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "array"},
                },
            },
        },
    )
    assert resp.status_code == 200
    value = resp.json()["value"]
    assert value["root_cause"]
    assert value["confidence"] > 0


def test_dspy_plan_shape():
    resp = client.post("/v1/dspy/plan", json={"context": "disk full on node-1"})
    assert resp.status_code == 200
    plan = resp.json()["value"]
    assert plan["owner"] == "planner"
    assert plan["plan"]


def test_rag_search_after_index():
    client.post("/v1/rag/index", json={})
    resp = client.post("/v1/rag/search", json={"query": "disk full", "top_k": 3})
    assert resp.status_code == 200
    hits = resp.json()["hits"]
    assert hits
    assert hits[0]["source"] == "pageindex"


def test_rag_index_populates_pages():
    resp = client.post("/v1/rag/index", json={})
    assert resp.status_code == 200
    assert resp.json()["pages"] >= 1


def test_analytics_reports_retrieval():
    client.post("/v1/rag/index", json={})
    client.post("/v1/rag/search", json={"query": "disk", "top_k": 2})
    body = client.get("/v1/analytics").json()
    assert body["retrieval_recall"] == 1.0
    assert body["chunks"]["count"] >= 1


def test_sse_events_stream():
    with client.stream("GET", "/v1/events/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = "".join(resp.iter_text())
        assert "event: done" in raw


def test_eval_lexical_metrics():
    resp = client.post(
        "/v1/eval",
        json={
            "questions": ["disk full?"],
            "answers": ["clean /var/log and rotate old logs"],
            "contexts": [["clean /var/log and rotate old logs"]],
            "ground_truths": [["clean /var/log"]],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["framework"] == "lexical"
    assert 0.0 <= body["faithfulness"] <= 1.0
    assert 0.0 <= body["context_recall"] <= 1.0
