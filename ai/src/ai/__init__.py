"""AI/LLM gateway for the multi-agent SRE workspace.

Composed of three layers that mirror the production deployment:

1. **Routing layer** — LiteLLM in front of OpenAI / Anthropic / vLLM / Ray Serve /
   Candle (OpenAI-compatible) providers, plus a deterministic local fallback.
   Every call is model-agnostic and traceable.
2. **Structured layer** — Instructor / Outlines / DSPy constrained generation so
   graph agents get schema-validated JSON, never free text.
3. **RAG layer** — hybrid retrieval fusing Qdrant (semantic vectors) with
   LanceDB (embedding table) and a SQLite + PageIndex vectorless index, fed by
   the document-ingestion pipeline (Chonkie -> offline hash embed -> index) and
   evaluated with RAGAS.

Embeddings are deterministic offline hashes (no torch/weights) and optional
extras (LiteLLM routing, vector stores, RAGAS) are imported lazily so the core
gateway — and its test suite — runs without GPU, weights or network access.
"""

__version__ = "1.0.0"
