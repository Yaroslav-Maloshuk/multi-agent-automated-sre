# ─────────────────────────────────────────────────────────────────────────
# Pingora reverse proxy — Rust edge in front of gateway / AI / inference.
# Multi-stage: alpine rust builder (cmake for zlib-ng) → minimal runtime.
# Build context is the repo root (same as worker/Dockerfile).
# ─────────────────────────────────────────────────────────────────────────
FROM rust:1.97-alpine AS build
RUN apk add --no-cache cmake clang musl-dev perl openssl-dev
WORKDIR /src
COPY backend/proxy/Cargo.toml backend/proxy/Cargo.lock ./
RUN mkdir src && printf 'fn main() {}\n' > src/main.rs \
    && cargo fetch \
    && cargo build --release --bin auto-sre-proxy \
    && rm -rf src
COPY backend/proxy/src ./src
RUN find src -type f -exec touch {} + \
    && cargo build --release --bin auto-sre-proxy && mv target/release/auto-sre-proxy /out-proxy

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
COPY --from=build /out-proxy /usr/local/bin/auto-sre-proxy
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/auto-sre-proxy"]
