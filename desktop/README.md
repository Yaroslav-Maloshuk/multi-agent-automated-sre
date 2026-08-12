# auto-sre-desktop

Minimal Wails v2 desktop client for the multi-agent-automated-sre gateway.

It calls the SRE gateway REST API at `http://localhost:8000/api/v1/incidents`
and displays incidents in a dark-themed table.

## Prerequisites

- Go 1.22+
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

## Notes

- Requires the SRE gateway running on `http://localhost:8000`.
- Not verified in CI here; the wails CLI toolchain is not installed in this repo.
