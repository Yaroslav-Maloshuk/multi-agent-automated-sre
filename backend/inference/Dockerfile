# ─────────────────────────────────────────────────────────────────────────
# Python-free local LLM inference — Candle/Qwen2 CPU serving an OpenAI
# compatible API. Heavy build (candle + tokenizers), tiny runtime.
# ─────────────────────────────────────────────────────────────────────────
FROM rust:1.85 AS build
WORKDIR /src
COPY backend/inference/Cargo.toml backend/inference/Cargo.lock ./
RUN mkdir src && printf 'fn main() {}\n' > src/main.rs \
    && cargo fetch \
    && cargo build --release --bin auto-sre-inference \
    && rm -rf src
COPY backend/inference/src ./src
RUN cargo build --release --bin auto-sre-inference && mv target/release/auto-sre-inference /out-inference

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /out-inference /usr/local/bin/auto-sre-inference
EXPOSE 11434
ENTRYPOINT ["auto-sre-inference"]
