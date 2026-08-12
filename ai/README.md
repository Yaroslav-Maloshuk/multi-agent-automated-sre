# auto-sre AI gateway

Python/FastAPI gateway that owns the AI/LLM slice of the stack.

| Concern | Technology |
|---|---|
| LLM routing | LiteLLM → OpenAI / Anthropic / vLLM / Ray Serve / Candle |
| Structured calling | Instructor + Outlines + DSPy (pydantic fallback) |
| Embeddings | sentence-transformers (local) / provider API, offline hash fallback |
| Chunking | Chonkie (RecursiveChunker), offline fallback splitter |
| Semantic store | Qdrant |
| Vector table | LanceDB |
| Vectorless RAG | SQLite + PageIndex (term-frequency, BM25-style) |
| Retrieval fusion | Reciprocal Rank Fusion across all three stores |
| Ingestion / ETL | Crawl4AI → chunk → embed → index; DuckDB analytics warehouse |
| Evaluation | RAGAS (faithfulness / relevancy / recall), offline lexical fallback |
| Streaming | SSE (`/v1/events/stream`, streaming chat) |

## Run

```bash
uv sync            # core deps (no torch/weights)
uv run uvicorn ai.main:app --port 8002
```

Optional extras (lazy-loaded, never required for the core):

```bash
uv sync --extra all        # litellm, instructor, outlines, dspy, torch/embeddings, qdrant, lancedb, crawl4ai, ragas
```

## Test / lint

```bash
uv run pytest -q
uv run ruff check src tests
uv run behave
```

Core tests need no Docker, no model weights and no external stores.
