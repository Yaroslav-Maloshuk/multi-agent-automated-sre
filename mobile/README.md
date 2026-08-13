# auto-sre mobile

Expo (SDK 53, TypeScript) mobile client for the multi-agent-automated-sre gateway.

It is an operator console on the go:

- **Live event stream** — streams `/ws/stream?channels=*` with auto-reconnect
  and exponential backoff; the incident list auto-refreshes when the stream
  reports activity.
- **Create incident** — POST `/api/v1/incidents` from the device.
- **Run the agent graph** — POST `/api/v1/incidents/{id}/run`.
- **HITL review** — approve / reject via `/api/v1/incidents/{id}/review`.
- **Interrupt** — stop a run via `/api/v1/incidents/{id}/interrupt`.
- **Incident detail** — live graph state for the selected incident.

## Run

- npx expo start
- npx expo start --ios / --android

## Environment

- API_BASE — REST base URL (default http://localhost:8000)
- WS_URL — WebSocket base URL (default ws://localhost:8000)
- EXPO_PUBLIC_API_BASE / EXPO_PUBLIC_WS_URL are inlined by Expo at build time.

## Notes

- No native build is verified in CI; run locally with Expo Go or a simulator.
