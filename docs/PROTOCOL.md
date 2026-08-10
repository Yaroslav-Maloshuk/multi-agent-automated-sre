# Real-time Protocol Contract (WebSocket + gRPC)

This document is the single source of truth for the real-time contract between
the frontend command center, the FastAPI gateway, and background workers.

## 1. Transport

| Surface      | Endpoint                                | Transport      |
|--------------|-----------------------------------------|----------------|
| REST         | `/api/v1/*`                             | HTTP           |
| WebSocket    | `/ws/stream`                            | WebSocket      |
| gRPC (opt.)  | `auto_sre.runtime.v1.RuntimeService`    | gRPC stream    |

The WebSocket JSON envelope and the gRPC `Envelope` are isomorphic; switching
between them requires no protocol redesign.

## 2. Channels

Every event is published to exactly one logical channel. The gateway
namespaces channel keys under `sre:` in Redis, and the frontend subscribes by
logical name.

| Channel              | Emitted by        | Content                                             |
|----------------------|-------------------|-----------------------------------------------------|
| `incident:<id>`      | graph / agents    | lifecycle, steps, logs, patch, HITL, tokens         |
| `run:<run_id>`       | graph             | run-scoped telemetry (alias of incident channel)    |
| `telemetry`          | ROI / tokens      | `telemetry.roi`, token spend aggregates             |
| `sandbox`            | sandbox pool      | acquire/release/stdout events                       |
| `system`             | gateway           | health, shutdown, heartbeat                         |

Client connects with `?channels=incident:inc_x,telemetry` and may subscribe /
unsubscribe at runtime.

## 3. Envelope (server → client)

```json
{
  "version": 1,
  "id": "evt_9f3c2a1b",
  "type": "graph.step.completed",
  "channel": "incident:inc_123",
  "correlation_id": "corr_...",
  "incident_id": "inc_123",
  "run_id": "run_...",
  "timestamp": "2026-08-07T12:00:00.000Z",
  "payload": { }
}
```

Event types (see `app/bus/schemas.py::EventType`):

- **Lifecycle:** `incident.created`, `incident.updated`
- **Graph:** `graph.started`, `graph.step.started`, `graph.step.completed`,
  `graph.status.changed`, `graph.completed`, `graph.failed`,
  `graph.interrupted`, `graph.resumed`
- **Agents:** `agent.started`, `agent.step`, `agent.log`, `agent.token_usage`
- **Patch / Shadow IDE:** `patch.generated`, `patch.typing`, `patch.diff`,
  `patch.applied`
- **HITL:** `hitl.pending_approval`, `hitl.review_decided`
- **Sandbox:** `sandbox.acquired`, `sandbox.released`, `sandbox.stdout`
- **Telemetry:** `memory.snapshot`, `telemetry.roi`

## 4. Client → server control messages

```json
{ "action": "subscribe",   "channel": "incident:inc_123" }
{ "action": "unsubscribe", "channel": "incident:inc_123" }
{ "action": "ping",        "payload": {} }
```

### 4.1 HITL decision (Approve / Reject / Modify)

```json
{
  "action": "hitl.review",
  "incident_id": "inc_123",
  "run_id": "run_...",
  "payload": {
    "patch_ref": "patch_...",
    "action": "approve",
    "reason": "looks good",
    "modified_files": { "src/main.py": "... new contents ..." }
  }
}
```

- `approve` → gate resolves, verifier runs, then deploy.
- `reject` → run transitions to `CANCELLED` (terminal).
- `modify` → the given files replace the patch content, then verification runs.

Also available as `POST /api/v1/incidents/{id}/review` with the same payload
minus the envelope.

### 4.2 Graph control

```json
{ "action": "graph.interrupt", "incident_id": "inc_123" }
{ "action": "graph.resume",    "incident_id": "inc_123" }
```

## 5. HITL state machine

```
CODE_FIXER ──▶ HITL_GATE ── approve/modify ──▶ VERIFIER ── ok ──▶ DEPLOY
                   │                                              (patch.applied)
                   ├── reject ──▶ CANCELLED
                   └── timeout  ──▶ CANCELLED
VERIFIER failure (attempts < max) ──▶ CODE_FIXER (with verifier_feedback)
```

The gate publishes `hitl.pending_approval` and the run enters
`PENDING_APPROVAL`. The frontend shows the diff; on decision the run resumes
from exactly where it stopped (an `asyncio.Future` held by the executor).

## 6. REST surface

| Method | Path                              | Purpose                          |
|--------|-----------------------------------|----------------------------------|
| GET    | `/api/v1/health`                  | liveness + dependency probes     |
| POST   | `/api/v1/incidents`               | ingest alert (`files` snapshot)  |
| GET    | `/api/v1/incidents`               | list                             |
| GET    | `/api/v1/incidents/{id}`          | full state + timeline            |
| POST   | `/api/v1/incidents/{id}/run`      | start graph (202, async)         |
| POST   | `/api/v1/incidents/{id}/review`   | HITL decision                    |
| POST   | `/api/v1/incidents/{id}/interrupt`| stop run                         |
| POST   | `/api/v1/incidents/{id}/resume`   | re-launch from last incident     |
| GET    | `/api/v1/agents`                  | agent catalog                    |
| GET    | `/api/v1/sandboxes`               | pool stats                       |
| POST   | `/api/v1/sandboxes/warm`          | force warm-up                    |
| GET    | `/api/v1/metrics/roi`             | ROI + token history (Postgres)   |
| GET    | `/api/v1/incidents/{id}/snapshots`| trajectory step refs (replay)    |
| GET    | `/api/v1/incidents/{id}/snapshots/{ref}` | full state at a step      |
| POST   | `/api/v1/incidents/{id}/fork`     | spawn child incident from a step |
| POST   | `/api/v1/collectors/datadog`      | Datadog event webhook            |
| POST   | `/api/v1/collectors/prometheus`   | Alertmanager webhook             |
| POST   | `/api/v1/collectors/kubernetes`   | Kubernetes Event webhook         |
| GET    | `/api/v1/collectors`              | enabled poller status            |

## 7. gRPC mapping

`backend/proto/runtime.proto` → `make proto-gen` produces stubs. `StreamEvents`
replaces `/ws/stream` for workers; `SubmitReview` maps to `hitl.review`.
