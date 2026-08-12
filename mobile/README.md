# auto-sre mobile

Expo (SDK 53, TypeScript) mobile client for the multi-agent-automated-sre gateway.

## Run

- npx expo start
- npx expo start --ios / --android

## Environment

- API_BASE — REST base URL (default http://localhost:8000)
- WS_URL — WebSocket base URL (default ws://localhost:8000)
- EXPO_PUBLIC_API_BASE / EXPO_PUBLIC_WS_URL are inlined by Expo at build time.

## Notes

- Fetches incidents from GET /api/v1/incidents and streams events from /ws/stream?channels=*.
- No native build is verified in CI; run locally with Expo Go or a simulator.
