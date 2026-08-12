# ─────────────────────────────────────────────────────────────────────────
# AI gateway (Python/FastAPI) — LiteLLM routing, structured calling,
# hybrid RAG, ETL and RAGAS evals. Build context is the repo root.
# Core deps only; heavy ML extras (torch/crawl4ai/ragas) are optional and
# lazy-loaded, so this image is small and starts instantly.
# ─────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

WORKDIR /app
COPY ai/pyproject.toml ai/.python-version ai/README.md ./
COPY ai/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8002
CMD ["uvicorn", "ai.main:app", "--host", "0.0.0.0", "--port", "8002"]
