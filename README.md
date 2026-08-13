<img src="https://github.com/Yaroslav-Maloshuk/multi-agent-automated-sre/blob/main/multi-agent-automated-sre.png" width="1000" height="1000">

# Multi-Agent Automated SRE Workspace

An autonomous, self-healing Site Reliability Engineering engine. It ingests
infrastructure alerts (Datadog, Prometheus/Alertmanager, Kubernetes, or any
`POST /api/v1/incidents`), reasons over the incident and production context
through a stateful **7-node async agent DAG**, reproduces and fixes the bug in
**isolated Docker sandboxes**, verifies the patch with real tests, and — only
after **human-in-the-loop approval** — deploys. Resolved incidents are indexed
into long-term memory and become prior art for future runs.

This is **not** a chatbot wrapper. It is a reactive execution engine: a **Go
gateway** (REST + WebSocket + SSE + Templ/HTMX dashboard), **Rust/Tokio agent
orchestration**, **Rust-native Candle inference**, a **Python/FastAPI AI
gateway** (LiteLLM routing, structured calling, hybrid RAG, document ingestion, RAGAS evals),
a **Pingora edge proxy**, and a **NATS JetStream** fabric where every sub-step,
token deduction and terminal byte streams to the React command center — and the
React Native mobile and Wails desktop consoles — in real time.

```
┌────────────┐   alert    ┌──────────────────────────────┐   WS/SSE   ┌─────────────────┐
│  Alerting  │ ─────────▶ │  Go Gateway (Goroutines)      │ ─────────▶ │  React Command  │
│  systems / │            │  /ws/stream + REST /v1        │            │  Center         │
│  webhooks  │            └───────┬──────────────────────┘            └─────────────────┘
└────────────┘                    │  publishes (sre.events)
                          ┌───────▼────────┐   NATS JetStream   ┌────────────┴───┐
                          │  Agent Graph   │ ────────────────▶ │  State / Logs  │
                          │  (Rust/Tokio)  │                   │  / Tokens /    │
                          └───────┬────────┘                   │  ROI metrics   │
                                  │                            └────────────────┘
        ┌───────────────┬─────────┼────────────┬────────────────┐
        ▼               ▼         ▼            ▼                ▼
  AlertIngest      Planner   LogAnalyzer   CodeFixer       HitlGate
        │            ┌────────┴───────┐      │   │              │
        │            │ (concurrent)   │      │   │              ▼
        └────────────┴────────────────┴──────┴───▶ Verifier ─▶ Deploy
                              (self-correct loop, up to 3 attempts)
```

## Agent pipeline

The graph is a typed, dependency-respecting DAG executed by the Rust worker
over NATS subject queues. Fan-out branches (`log_analyzer` and `code_fixer`)
run concurrently and merge deterministically; the verifier self-correction loop
re-queues fixer → verifier up to `GRAPH_MAX_VERIFIER_ATTEMPTS` (default 3).

| Node | Role |
|---|---|
| `alert_ingest` | Normalize + admit the alert, attach incident context |
| `planner` | Investigative plan + subtasks; grounded with RAG prior art |
| `log_analyzer` | Root-cause hypothesis from logs / incident evidence |
| `code_fixer` | Generates a unified diff, runs it inside a sandbox |
| `hitl_gate` | Blocks until a signed review (approve / reject / modify) |
| `verifier` | Executes the real test suite in the sandbox, reports pass/fail |
| `deploy` | Marks the patch applied, calls the CD webhook, completes |

Every transition publishes a `memory.snapshot` — the full agent state is
persisted to Postgres and mirrored to Qdrant, giving the UI time-travel replay
and snapshot **forking**.

## Architecture

| Layer | Technology |
|---|---|
| Edge Proxy | Pingora (Rust, Cloudflare's proxy framework) — path-based routing to gateway / AI / Candle |
| API Gateway | Go (Goroutines/Channels), REST `/api/v1`, typed WebSocket, SSE, Templ+HTMX dashboard |
| Agent Orchestration | Rust/Tokio async DAG executor (interrupts via CancellationToken) |
| Isolation | Docker SDK — pre-warmed, network-restricted sandbox pool (`--network none`) |
| Background Execution | NATS JetStream — work-queue dispatch to a Rust worker pool via subject queues |
| Real-time Fabric | NATS → WebSocket + SSE fan-out (zero polling) |
| LLM Routing | LiteLLM via the AI gateway: one OpenAI-compatible surface over OpenAI / Anthropic / vLLM / Ray Serve / Candle |
| LLM Serving | Candle (Rust-native, `:11434`), vLLM (`:8001`), Ray Serve (vLLM engine, OpenAI API, `:8002`) |
| Structured Calling | Instructor + Outlines + DSPy (constrained JSON, pydantic fallback) |
| RAG | Qdrant (semantic) + LanceDB (vector table) + SQLite PageIndex (vectorless), RRF fusion, Chonkie chunking |
| Ingestion / Analytics | raw text documents → Chonkie → embed → index; DuckDB columnar warehouse |
| LLM Evaluation | RAGAS (faithfulness / relevancy / recall), offline lexical fallback |
| Persistence | Go/SQL + PostgreSQL — incidents, live state, durable ROI ledger, snapshots |
| Frontend (web) | React 19 + TypeScript + Tailwind + Recharts + Monaco diff + Xterm |
| Frontend (server) | Templ + HTMX (SSE-driven dashboard at `/`) |
| Mobile | React Native (Expo, `mobile/`) — WS event stream, create/run/review/interrupt |
| Desktop | Wails v2 (Go + React, `desktop/`) — same live operator console |
| Observability | Structured JSON logging + optional Langfuse tracing hooks |
| CI/CD | GitHub Actions (CI quality gate, Docker build/push, desktop + Expo bundles), Dependabot |
| Testing | Pytest + Ruff + BDD (behave) + RAGAS, Go tests (`-race`), Rust clippy/tests, K6 load/contract, chaos soak, Postman collection |

## Quickstart (one command)

```bash
docker compose up --build
```

This brings up the whole stack: NATS (JetStream bus + agent work-queue), Valkey
(cache / reactive store), Qdrant (snapshots + long-term memory), Postgres
(incidents + ROI ledger + snapshots), the Candle inference server (Python-free
local LLM on `:11434`), the Pingora edge proxy, the Go gateway, the Rust worker
pool, the FastAPI AI gateway, and the React command center. A one-shot
`sandbox-image` step builds the isolated agent sandbox image
(`auto-sre/sandbox:dev`) from `sandbox/Dockerfile` before the worker starts, so
code reproduction and verification work out of the box.

Optional LLM tiers (profiles):

```bash
docker compose --profile llm-routing up -d  # LiteLLM proxy on :4000
docker compose --profile gpu-llm up -d      # vLLM :8001 + Ray Serve (vLLM engine, OpenAI API) :8002 + Ray dashboard :8265
```

`LLM_PROVIDER=local` (default) points the worker at Candle inference
(`:11434`, note the `/v1` base URL). Set `LLM_PROVIDER=ai` to reason through
the AI gateway instead: every agent LLM call goes to `AI_URL` (`:8002`, LiteLLM
routing + structured calling), agent retrieval uses the gateway's hybrid RAG
(`/v1/rag/search`, Qdrant + LanceDB + PageIndex with RRF), and embeddings fall
back to the gateway's `/v1/embeddings`. `openai` / `anthropic` are available
with their API keys. To route gateway LLM calls through the optional LiteLLM
proxy (`:4000`) instead of reaching providers directly, set `AI_PROXY_URL`.

| What | URL |
|---|---|
| Command center (React) | http://localhost:3000 (compose) · :5173 (vite dev) |
| Templ + HTMX dashboard | http://localhost:8000 |
| API + WebSocket + SSE | http://localhost:8000 |
| AI gateway (OpenAI-compatible, RAG, evals) | http://localhost:8002 |
| Edge proxy (Pingora) | http://localhost:8080 |
| Candle inference (local LLM) | http://localhost:11434 |
| NATS monitor | http://localhost:8222 |

Optional env overrides live in `.env` (see `.env.example`); the stack runs
sane defaults without it.

Local development (without Docker for the app itself):

```bash
cp .env.example .env
make infra                          # docker compose up -d nats valkey qdrant postgres
make gateway                        # go run ./cmd/gateway on :8000
make ai                             # uv run uvicorn on :8002
make proxy                          # cargo run (Pingora) on :8080
make worker                         # cargo run in backend/worker
make local-llm                      # Candle inference on :11434 (for local dev only)
make frontend                       # vite on :5173
make mobile                         # React Native (Expo)
make desktop                        # Wails desktop app
```

Unit tests need no Docker/NATS/LLM: `make test`.

## API surface (Go gateway, `:8000`)

- `GET  /api/v1/health` — liveness + dependency probes
- `POST /api/v1/incidents` — ingest an infra alert, returns `Incident`
- `GET  /api/v1/incidents` — list incidents
- `GET  /api/v1/incidents/{id}` — full state + timeline
- `POST /api/v1/incidents/{id}/run` — start the agent graph (NATS dispatch)
- `POST /api/v1/incidents/{id}/review` — HITL approve / reject / modify
- `POST /api/v1/incidents/{id}/interrupt` — stop an in-flight run
- `POST /api/v1/incidents/{id}/resume` — re-launch from the last incident payload
- `GET  /api/v1/incidents/{id}/snapshots` — trajectory step refs (time-travel)
- `GET  /api/v1/incidents/{id}/snapshots/{ref}` — a full step snapshot
- `POST /api/v1/incidents/{id}/fork` — spawn a child incident from a snapshot
- `GET  /api/v1/agents` — agent catalog
- `GET  /api/v1/sandboxes` — pool stats
- `POST /api/v1/sandboxes/warm` — force pool warm-up
- `GET  /api/v1/metrics/roi` — cost-savings + token ledger (persisted in Postgres)
- `POST /api/v1/collectors/datadog` — Datadog event webhook
- `POST /api/v1/collectors/prometheus` — Alertmanager webhook
- `POST /api/v1/collectors/kubernetes` — Kubernetes Event webhook
- `GET  /api/v1/collectors` — enabled poller status
- `WS   /ws/stream?channels=...` — bidirectional event channel
- `SSE  /api/v1/events/stream?channels=...` — same fabric as SSE (HTMX dashboard)
- `GET  /` — server-rendered Templ + HTMX dashboard
- `GET  /api/v1/dashboard/incidents` — HTMX-swappable incidents fragment

Alerts are ingested natively from Datadog, Prometheus/Alertmanager and
Kubernetes — both push (webhooks above) and pull (periodic pollers for Datadog
monitors and Prometheus `ALERTS`). Full contract in [docs/PROTOCOL.md](docs/PROTOCOL.md).

### AI gateway (`:8002`)

- `GET   /health`, `GET /v1/providers` — routing health + LiteLLM provider list
- `POST  /v1/chat/completions` — OpenAI-compatible chat (SSE stream when `stream=true`)
- `POST  /v1/embeddings` — deterministic offline hash embeddings
- `POST  /v1/structured` — Instructor/Outlines/DSPy constrained JSON
- `POST  /v1/dspy/plan` — DSPy planner module
- `POST  /v1/rag/search` — hybrid RRF over Qdrant + LanceDB + SQLite PageIndex
- `POST  /v1/rag/documents` — ingest a raw postmortem / runbook / note
- `GET   /v1/analytics` — DuckDB warehouse: recall, chunk stats, recent retrievals
- `POST  /v1/eval` — RAGAS metrics (faithfulness / relevancy / recall)
- `GET   /v1/events/stream` — SSE for long-running AI jobs

The container image ships the `routing` (LiteLLM / Instructor / Outlines),
`vector` (Qdrant / LanceDB) and `eval` (RAGAS) extras, so chat, hybrid RAG and
RAGAS evals work out of the box. Embeddings are deterministic offline hashes —
the gateway has no torch/weights and no web-crawler (Crawl4AI) dependency.

REST/WS collection in [postman/auto-sre.postman_collection.json](postman/auto-sre.postman_collection.json).

## Real-time events

Every graph/agent transition is a typed envelope on a logical channel
(`incident:<id>`, `run:<run_id>`, `sandbox`, `system`, …) fanned out over
WebSocket and SSE. ~27 server event types plus 3 client control actions
(`hitl.review`, `graph.interrupt`, `graph.resume`):

- **Lifecycle:** `incident.created`, `incident.updated`
- **Graph:** `graph.started`, `graph.step.started`, `graph.step.completed`,
  `graph.status.changed`, `graph.completed`, `graph.failed`,
  `graph.interrupted`, `graph.resumed`
- **Agents:** `agent.started`, `agent.step`, `agent.log`, `agent.token_usage`
- **Patch:** `patch.generated`, `patch.typing`, `patch.diff`, `patch.applied`
- **HITL:** `hitl.pending_approval`, `hitl.review_decided`
- **Sandbox:** `sandbox.acquired`, `sandbox.released`, `sandbox.stdout`
- **Memory / telemetry:** `memory.snapshot`, `telemetry.roi`, `system.ready`, `system.pong`

## Key features

1. **Stateful agent DAG** — AlertIngest → Planner → Log Analyzer / Code Fixer
   (concurrent) → HITL gate → Verifier (self-correct loop) → Deploy, each node
   a typed async actor (Rust/Tokio), dispatched over NATS subjects.
2. **Snapshot engine** — every graph step persists a full state snapshot
   (`memory.snapshot`, Postgres `snapshots` + Qdrant mirror); the API exposes
   snapshots and the UI lets you click any timeline step to **replay** it and
   **fork** a child incident from that exact trajectory point.
3. **Learning loop** — resolved incidents (root cause + applied fix) are
   indexed into long-term memory and surface as RAG prior art for future
   planner / log-analyzer / fixer runs.
4. **Diff streaming** — the fixer streams live `patch.typing` telemetry and a
   unified Git-style diff (`patch.diff`); the UI renders before/after pairs in
   a **Monaco diff editor** and the run console streams into an **Xterm**
   terminal.
5. **Warm sandbox pool** — 3–5 pre-warmed, network-restricted containers
   (`--network none`, CPU/memory quotas); code volumes mount in milliseconds.
6. **Reactive sync** — every event flows NATS → WebSocket/SSE, zero polling.
7. **ROI ledger** — latency + token spend converted into concrete $ savings,
   persisted to Postgres so history survives restarts.

## Testing

```bash
make test          # pytest + behave + go test -race + cargo test ×3 (no Docker/NATS/LLM)
make smoke         # E2E smoke against a running stack (tests/e2e/smoke.sh)
make contract      # Go contract tests + K6 against a live gateway
make k6 / load     # K6 — concurrent REST + WS load
make chaos         # worker partition/restart soak via tests/chaos/soak.sh
make lint          # ruff + gofmt/vet + cargo clippy --all-targets + eslint
make typecheck     # clippy + tsc
```

All pass on `main` (verified 2026-08-13).

## Measured metrics

### Engineering metrics

| Metric | Value |
|---|---|
| Go gateway LOC (app / tests) | 2,638 / 746 |
| Rust LOC (worker + inference + proxy) | 4,517 (incl. tests) |
| Python AI gateway LOC (app / tests) | 1,484 / 452 |
| Frontend LOC | 2,190 TS/TSX |
| Tests | `go test -race` (8 pkgs) · `cargo test` (22) · pytest (35) · behave (2 scenarios / 8 steps) |
| Lint | 0 issues (ruff + gofmt/vet + clippy `--all-targets` + eslint) |
| Typecheck / build | 0 issues (clippy + tsc + vite build) |
| REST endpoints | 21 (`/api/v1/*`) |
| Real-time endpoints | WebSocket `/ws/stream` + SSE `/api/v1/events/stream` |
| Event types | 27 server → client (+ 3 client control actions) |
| Graph nodes | 7 (AlertIngest → Planner → LogAnalyzer / CodeFixer → HITL → Verifier → Deploy) |
| Agent implementations | 5 (+ ingest + HITL gate infra nodes, registry catalog) |
| Sandbox pool | 3–5 warm, `--network none`, 512 MB / 1.5 CPU quota |

### Business / ROI model

The engine converts engineering latency into money via a config-driven ledger
(`GET /api/v1/metrics/roi`):

| Parameter | Value |
|---|---|
| Human MTTR baseline | 35 min (`HUMAN_MTTR_SECONDS=2100`) |
| Loaded engineer cost | $150 / h (`HUMAN_LOADED_COST_PER_HOUR`) |
| Token price (in / out per 1K) | $0.005 / $0.015 |
| Max tokens per run | 200,000 |

Per completed incident the ledger reports duration vs. the 35-min baseline,
token spend, and `saved_amount = saved_hours × $150/h − token_cost`. The ROI
ledger is durable (Postgres `roi_ledger` table) — history survives restarts.

> **Honest caveats:** ROI entries are only recorded for runs that reach a
> terminal status (COMPLETED / FAILED / CANCELLED / INTERRUPTED), and the
> "savings" figure assumes a fully autonomous, approved run (saved time is only
> counted for COMPLETED runs).

## Security boundaries

- Agent code execution **only** inside `sandbox/` Docker containers with
  `--network none`, CPU/memory limits and read-only rootfs by default.
- No arbitrary string execution on the host; all mutation is diff-based and
  gated by explicit human approval before deploy.
- NATS fabric is auth-gated; subjects are namespaced per environment.
- Secrets live only in `.env`; never in code or images.
