# auto-sre-desktop

Wails v2 desktop client for the multi-agent-automated-sre gateway.

Beyond listing incidents it is a live operator console:

- **Live event stream** — forwards the gateway WebSocket fabric (`/ws/stream`)
  into the React UI via `runtime` events (`sre:event`, `sre:connection`), with
  auto-reconnect and backoff.
- **Create incident** — POST `/api/v1/incidents` from the desktop.
- **Run the agent graph** — POST `/api/v1/incidents/{id}/run` (NATS dispatch).
- **HITL review** — approve / reject on `/api/v1/incidents/{id}/review`.
- **Interrupt** — stop an in-flight run via `/api/v1/incidents/{id}/interrupt`.
- **Incident detail** — full payload + live graph state + ROI ledger.

## Prerequisites

- Go 1.23+
- Node.js 18+
- Wails CLI: `go install github.com/wailsapp/wails/v2/cmd/wails@v2.9.2`

## Run (development)

```sh
wails dev
```

Launches the app with live frontend reload on port 5173.

## Build

```sh
wails build
```

Produces a native binary in `build/bin/`.

## Environment

- `SRE_GATEWAY_URL` — REST base URL (default `http://localhost:8000`)
- `SRE_WS_URL` — WebSocket base URL (default `ws://localhost:8000`)

## Notes

- Requires the SRE gateway running on `http://localhost:8000`.
- The embedded frontend is built with Vite (`desktop/frontend/`); `wails build`
  runs `npm install && npm run build` before the Go embed step.
- Not verified in CI here; the wails CLI toolchain is not installed in this repo.
