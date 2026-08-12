# ─────────────────────────────────────────────────────────────────────────
# Rust worker pool — stateful agent graph, NATS consumer, Qdrant memory,
# Docker sandbox pool (bollard over the host docker socket).
# Multi-stage: debian rust builder → minimal bookworm runtime.
# ─────────────────────────────────────────────────────────────────────────
FROM rust:1.85 AS build
WORKDIR /src
COPY backend/worker/Cargo.toml backend/worker/Cargo.lock ./
RUN mkdir src && printf 'fn main() {}\n' > src/main.rs \
    && cargo fetch \
    && cargo build --release --bin auto-sre-worker \
    && rm -rf src
COPY backend/worker/src ./src
RUN cargo build --release --bin auto-sre-worker && mv target/release/auto-sre-worker /out-worker

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /out-worker /usr/local/bin/auto-sre-worker
ENTRYPOINT ["auto-sre-worker"]
