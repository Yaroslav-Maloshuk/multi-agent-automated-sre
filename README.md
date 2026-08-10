# Multi-Agent Automated SRE Workspace

An autonomous, self-healing Site Reliability Engineering engine. It ingests
infrastructure alerts, reasons over production logs via a stateful **async
multi-agent graph**, reproduces bugs in **isolated Docker sandboxes**, writes
and verifies code patches, and — after **human-in-the-loop approval** — deploys.

This is **not** a chatbot wrapper. It is a reactive execution engine whose every
sub-step, token deduction and terminal byte is streamed to a React command
center over WebSockets in real time.

```
┌────────────┐   alert    ┌──────────────────────────────┐   WS      ┌─────────────────┐
│  Alerting  │ ─────────▶ │  FastAPI Gateway (async)     │ ─────────▶ │  React Command  │
│  systems / │            │  /ws/stream  +  REST /v1     │            │  Center         │
│  webhooks  │            └───────┬──────────────────────┘            │  (Recharts)     │
└────────────┘                    │                                   └─────────────────┘
                                  │ publishes
                          ┌───────▼────────┐   Redis Pub/Sub   ┌────────────┴───┐
                          │  Agent Graph   │ ────────────────▶ │  State / Logs  │
                          │  (asyncio DAG) │                   │  / Tokens /    │
                          └───────┬────────┘                   │  ROI metrics   │
                                  │                            └────────────────┘
        ┌───────────────┬─────────┼────────────┬────────────────┐
        ▼               ▼         ▼            ▼                ▼
   Planner        Log Analyzer   Code Fixer   Verifier      HITL Gate
        │               │         (diffs in     (pytest in      │
        │               │          sandbox)      sandbox)       ▼
        │               │                                      Deploy
        └───────────────┴────────────────────────────────────────────
```

Alerts are ingested natively from Datadog, Prometheus/Alertmanager and
Kubernetes — both push (`POST /api/v1/collectors/{source}` webhooks) and pull
(periodic pollers for Datadog monitors, Prometheus `ALERTS` and K8s Warning
events). `POST /api/v1/incidents` remains the generic programmatic entry point.

## Architecture (top-level)

| Layer | Technology |
|---|---|
| API Gateway | Python 3.11+ / FastAPI (async), typed WebSocket protocol |
| Agent Orchestration | Asyncio state graph (DAG executor with interrupts) |
| Background Execution | Celery (broker + result backend: Redis). Runs dispatch to the worker pool when `CELERY_ENABLED=true`; local dev runs in-process |
| Real-time Fabric | Redis Pub/Sub → WebSocket fan-out (no SSE) |
| Isolation | Docker SDK — pre-warmed, network-restricted sandbox pool (`--network none`) |
| Long-term Memory | PostgreSQL + pgvector (semantic) + BM25/tsvector (lexical), RRF fusion |
| LLMs | OpenAI / Anthropic / local HuggingFace (provider-agnostic, lazy-imported) |
| Persistence | PostgreSQL via Prisma ORM (+ raw asyncpg for ROI ledger) |
| Frontend | React 18 + TypeScript + Tailwind + Recharts + Monaco diff + Xterm |
| Observability | Structured JSON logging + Langfuse traces (optional, env-gated) |
| CI/CD | GitHub Actions (CI quality gate + Docker build/push to GHCR), Dependabot |
| Testing | Pytest 30 tests, ruff, mypy (strict), tsc |

## Quickstart (one command)

```bash
docker compose up --build
```

This single command brings up the whole stack: Redis (pub/sub + broker),
Postgres + pgvector (persistence + hybrid memory), the FastAPI gateway
(auto-applies the Prisma schema), the Celery worker, and the React command
center. On first start the backend auto-builds the isolated agent sandbox
image (`auto-sre/sandbox:dev`) via the host Docker daemon.

- Command center → http://localhost:5173
- API + WebSocket → http://localhost:8000  (`/docs` for OpenAPI)

Optional env overrides live in `.env` (see `.env.example`); the stack runs
sane defaults without it.

Local development (without Docker for the app itself) is still supported:

```bash
cp .env.example .env
docker compose up -d redis postgres   # infra only
make backend                          # uvicorn --reload on :8000
make frontend                         # vite on :5173
```

The pytest suite needs no Docker/Redis/LLM: `make test`.

## API surface

- `GET  /api/v1/health` — liveness + dependency probes
- `POST /api/v1/incidents` — ingest an infra alert, returns `Incident`
- `GET  /api/v1/incidents` — list incidents
- `GET  /api/v1/incidents/{id}` — full state + timeline
- `POST /api/v1/incidents/{id}/run` — start the agent graph
- `POST /api/v1/incidents/{id}/review` — HITL approve / reject / modify
- `POST /api/v1/incidents/{id}/interrupt` — stop an in-flight run
- `POST /api/v1/incidents/{id}/resume` — re-launch from the last incident payload
- `GET  /api/v1/agents` — agent catalog
- `GET  /api/v1/sandboxes` — pool stats
- `POST /api/v1/sandboxes/warm` — force pool warm-up
- `GET  /api/v1/metrics/roi` — cost-savings + token ledger (persisted in Postgres)
- `GET  /api/v1/incidents/{id}/snapshots` — trajectory step refs (time-travel)
- `GET  /api/v1/incidents/{id}/snapshots/{ref}` — a full step snapshot
- `POST /api/v1/incidents/{id}/fork` — spawn a child incident from a snapshot
- `POST /api/v1/collectors/datadog` — Datadog event webhook
- `POST /api/v1/collectors/prometheus` — Alertmanager webhook
- `POST /api/v1/collectors/kubernetes` — Kubernetes Event webhook
- `GET  /api/v1/collectors` — enabled poller status
- `WS   /ws/stream?channels=...` — bidirectional event channel

Full contract in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## The kill-features

1. **Stateful agent DAG** — Planner → Log Analyzer → Code Fixer → HITL gate →
   Verifier (self-correct loop) → Deploy, each node a typed async actor.
2. **Snapshot engine** — every graph step persists a full state snapshot
   (`memory.snapshot`); the API exposes `get_snapshot` and the UI lets you click
   any timeline step to **replay** it and **fork** a child incident from that
   exact trajectory point.
3. **Diff streaming** — the fixer streams live "typing" telemetry and a unified
   Git-style diff; the UI renders before/after file pairs in a **Monaco diff
   editor** and the run console streams into an **Xterm** terminal.
4. **Warm sandbox pool** — 3–5 pre-warmed, network-restricted containers; code
   volumes mount in milliseconds.
5. **Reactive sync** — every event flows Redis Pub/Sub → WebSocket, zero polling.
6. **ROI ledger** — latency + token spend converted into concrete $ savings,
   persisted to Postgres so history survives restarts.

## Testing

```bash
make test          # pytest (core suite needs no Docker/Redis/LLM)
make lint          # ruff + eslint
make typecheck     # mypy + tsc
```

All three pass on `main` (verified 2026-08-10).

## Measured metrics (2026-08-10)

### Engineering metrics

| Metric | Value |
|---|---|
| Backend LOC (app) | 5,518 Python |
| Backend LOC (tests) | 651 Python |
| Frontend LOC | 2,183 TS/TSX |
| Tests | 30 passed (~6 s, no infra required) |
| Test coverage | 74 % statement (`pytest --cov`) |
| Lint | 0 issues (ruff + eslint) |
| Typecheck | 0 issues (mypy strict + tsc) |
| REST endpoints | 19 (`/api/v1/*`) |
| WebSocket endpoint | 1 (`/ws/stream`) |
| Event types | 25 |
| Graph nodes | 7 (Ingest → Planner → Log Analyzer / Code Fixer → HITL → Verifier → Deploy) |
| Agent implementations | 5 (+ registry catalog) |
| Backend deps | 17 runtime + 7 dev |
| Frontend deps | 11 runtime + 12 dev |
| Sandbox pool | 3–5 warm, `--network none`, 512 MB / 1.5 CPU quota |
| Local model cache | Qwen2.5-3B-Instruct — 10 GB on host, auto-mount into backend |

### Runtime / LLM performance (local provider, CPU-only container)

| Measurement | Result |
|---|---|
| Cold start (model load from cache) | ~30–60 s |
| Throughput (256-token generation) | ~1.3 tokens/s |
| 166-token response wall time | ~129 s |

Note: on CPU a full agent run needs several minutes; GPU or an API provider is
recommended for production.

### Business / ROI model

The engine converts engineering latency into money via a config-driven ledger
(`backend/app/services/roi.py`, surfaced at `GET /api/v1/metrics/roi`):

| Parameter | Value |
|---|---|
| Human MTTR baseline | 35 min (`human_mttr_seconds`) |
| Loaded engineer cost | $150 / h |
| Token price (in / out per 1K) | $0.005 / $0.015 |
| Max tokens per run | 200,000 |

Per completed incident the ledger reports: duration vs. the 35-min baseline,
token spend, and the net `saved_amount = saved_hours × $150/h − token_cost`.
At current local-model pricing token cost is negligible (cents), so the ceiling
per incident is ≈ $87.5 of labor saved minus tokens.

> **Honest caveats:** ROI entries are only recorded for runs that reach a
> terminal status (COMPLETED / FAILED / CANCELLED), and the "savings" figure
> assumes a fully autonomous, approved run. The ledger itself is now durable
> (Postgres `roi_ledger` table) — history survives restarts.

## Security boundaries

- Agent code execution **only** inside `sandbox/` Docker containers with
  `--network none`, CPU/memory limits and read-only mounts by default.
- No arbitrary string execution on the host; all mutation is diff-based and
  gated by explicit human approval before deploy.
- Secrets live only in `.env`; never in code or images.
