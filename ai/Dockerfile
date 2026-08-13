# ─────────────────────────────────────────────────────────────────────────
# AI gateway (Python/FastAPI) — LiteLLM routing, structured calling,
# hybrid RAG and RAGAS evals. Build context is the repo root.
# The container ships the `routing` (LiteLLM/Instructor/Outlines), `vector`
# (Qdrant/LanceDB) and `eval` (RAGAS) extras so chat, hybrid RAG and evals
# work out of the box. Embeddings are deterministic offline hashes; there are
# no torch/weights and no web-crawler dependencies.
# ─────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

WORKDIR /app
COPY ai/pyproject.toml ai/uv.lock ai/.python-version ai/README.md ./
COPY ai/src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra routing --extra vector --extra eval

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8002
CMD ["uvicorn", "ai.main:app", "--app-dir", "/app/src", "--host", "0.0.0.0", "--port", "8002"]
