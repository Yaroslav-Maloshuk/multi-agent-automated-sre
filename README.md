# Multi-Agent Automated SRE Workspace

An autonomous, self-healing Site Reliability Engineering engine. It ingests
infrastructure alerts, reasons over production logs via a stateful **async
multi-agent graph**, reproduces bugs in **isolated Docker sandboxes**, writes
and verifies code patches, and — after **human-in-the-loop approval** — deploys.

This is **not** a chatbot wrapper. It is a reactive execution engine whose every
sub-step, token deduction and terminal byte is streamed to a React command
center over WebSockets in real time.

```
┌────────────┐   alert    ┌──────────────────────────────┐   WS / SSE   ┌─────────────────┐
│  Datadog / │ ─────────▶ │  FastAPI Gateway (async)     │ ───────────▶ │  React Command  │
│  Prom /    │            │  /ws/stream  +  REST /v1     │              │  Center         │
│  K8s / CI  │            └───────┬──────────────────────┘              │  (React Flow,   │
└────────────┘                    │                                    │   Monaco, Xterm,│
                                  │ publishes                           │   Recharts)     │
                          ┌───────▼────────┐   Redis Pub/Sub   ┌────────────┴───┐
                          │  Agent Graph   │ ────────────────▶ │  State / Logs  │
                          │  (asyncio DAG) │                   │  / Tokens /    │
                          └───────┬────────┘                   │  ROI metrics   │
                                  │                            └────────────────┘
        ┌───────────────┬─────────┼────────────┬────────────────┐
        ▼               ▼         ▼            ▼                ▼
   Planner        Log Analyzer   Code Fixer   Verifier      HITL Gate
        │               │         (Monaco      (pytest in      │
        │               │          cursor +     sandbox)        │
        │               │          diffs)                       ▼
        │               │                                     Deploy
        └───────────────┴────────────────────────────────────────────
```

## Architecture (top-level)

| Layer | Technology |
|---|---|
| API Gateway | Python 3.11+ / FastAPI (async), typed WebSocket protocol |
| Agent Orchestration | Asyncio state graph (DAG executor with interrupts) |
| Background Execution | Celery (broker: Redis) |
| Real-time Fabric | Redis Pub/Sub → WebSocket / SSE fan-out |
| Isolation | Docker SDK — pre-warmed, network-restricted sandbox pool |
| Long-term Memory | PostgreSQL + pgvector (embeddings) + BM25 (tsvector), RRF fusion |
| LLMs | OpenAI / Anthropic / local HuggingFace (provider-agnostic) |
| Persistence | PostgreSQL via Prisma ORM |
| Frontend | React 18 + TypeScript + Tailwind + React Flow + Monaco + Xterm + Recharts |
| Observability | Structured JSON logging, Langfuse (LLMOps), GitHub Actions |
| Testing | Pytest (TDD), HTTPX + pytest-asyncio |

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
- `POST /api/v1/incidents/{id}/run` — start the agent graph
- `GET  /api/v1/incidents/{id}` — full state + timeline
- `POST /api/v1/incidents/{id}/review` — HITL approve / reject / modify
- `GET  /api/v1/agents` — agent catalog
- `POST /api/v1/sandboxes/warm` — force pool warm-up
- `GET  /api/v1/metrics/roi` — cost-savings + token ledger
- `WS   /ws/stream?channels=...` — bidirectional event channel

Full contract in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## The kill-features

1. **Stateful agent DAG** — Planner → Log Analyzer → Code Fixer → HITL gate →
   Verifier (self-correct loop) → Deploy, each node a typed async actor.
2. **Time-travel debugger** — every graph step persists a memory + log snapshot;
   the UI can replay step X and fork the trajectory.
3. **Shadow IDE** — Monaco renders the fixer's live typing cursor and Git-style
   side-by-side diffs.
4. **Warm sandbox pool** — 3–5 pre-warmed, network-restricted containers; code
   volumes mount in milliseconds.
5. **Reactive sync** — every event flows Redis Pub/Sub → WebSocket, zero polling.
6. **ROI ledger** — latency + token spend converted into concrete $ savings.

## Testing

```bash
make test          # pytest (core suite needs no Docker/Redis/LLM)
make lint          # ruff + eslint
make typecheck     # mypy + tsc
```

## Security boundaries

- Agent code execution **only** inside `sandbox/` Docker containers with
  `--network none`, CPU/memory limits and read-only mounts by default.
- No arbitrary string execution on the host; all mutation is diff-based and
  gated by explicit human approval before deploy.
- Secrets live only in `.env`; never in code or images.
