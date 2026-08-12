"""AI/LLM gateway for the multi-agent SRE workspace.

Composed of three layers that mirror the production deployment:

1. **Routing layer** — LiteLLM in front of OpenAI / Anthropic / vLLM / Ray Serve /
   Candle(OpenAI-compatible) providers, plus a local sentence-transformers
   embedding fallback. Every call is model-agnostic and traceable.
2. **Structured layer** — Instructor / Outlines / DSPy constrained generation so
   graph agents get schema-validated JSON, never free text.
3. **RAG layer** — hybrid retrieval fusing Qdrant (semantic vectors) with
   LanceDB (embedding table) and a SQLite + PageIndex vectorless index, fed by
   a Crawl4AI -> Chonkie -> embed -> index ETL pipeline, evaluated with RAGAS.

Heavy optional dependencies (torch-based ML, crawlers, evals) are imported
lazily so the core gateway — and its test suite — runs without GPU/weights.
"""

__version__ = "1.0.0"
