# ─────────────────────────────────────────────────────────────────────────
# Go gateway — REST API + WebSocket hub + NATS fan-out.
# Multi-stage: alpine builder → distroless runtime (no shell, no Python).
# ─────────────────────────────────────────────────────────────────────────
FROM golang:1.26-alpine AS build
WORKDIR /src
COPY backend/go.mod backend/go.sum ./
RUN go mod download
COPY backend/ ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/gateway ./cmd/gateway

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/gateway /gateway
EXPOSE 8000
ENTRYPOINT ["/gateway"]
