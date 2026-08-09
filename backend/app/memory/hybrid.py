"""Hybrid long-term memory: pgvector (semantic) + BM25/tsvector (lexical).

Both retrievers are fused with Reciprocal Rank Fusion so production log
context is neither blind to exact error strings nor to semantic paraphrase.
`InMemoryMemoryStore` provides the identical interface for tests/dev without
Postgres.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import uuid4

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class MemoryHit:
    id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class Embedder(ABC):
    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    @property
    @abstractmethod
    def dimension(self) -> int:
        raise NotImplementedError


class HashingEmbedder(Embedder):
    """Deterministic feature-hash embedding (char n-gram hashing).

    A genuine lexical embedding used as the default and fallback — no model
    download required, fully reproducible across restarts.
    """

    DIMENSION = 1536

    def __init__(self, dimension: int = DIMENSION) -> None:
        self._dim = dimension
        self._lock = threading.Lock()

    @property
    def dimension(self) -> int:
        return self._dim

    def _n_grams(self, text: str, n: int = 3) -> list[str]:
        tokens = re.findall(r"[a-zA-Z0-9_]+", text.lower())
        grams: list[str] = []
        for token in tokens:
            padded = f"^{token}$"
            grams.extend(padded[i : i + n] for i in range(max(1, len(padded) - n + 1)))
        return grams

    async def embed(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed, text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for gram in self._n_grams(text):
            digest = hashlib.blake2b(gram.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        return vector


class OpenAIEmbedder(Embedder):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def dimension(self) -> int:
        return 1536

    async def embed(self, text: str) -> list[float]:
        from openai import AsyncOpenAI

        assert self._settings.openai_api_key, "OPENAI_API_KEY not configured"
        client = AsyncOpenAI(api_key=self._settings.openai_api_key)
        resp = await client.embeddings.create(model="text-embedding-3-small", input=[text])
        return list(resp.data[0].embedding)


def _embedder_for(settings: Settings) -> Embedder:
    return OpenAIEmbedder(settings) if settings.openai_api_key else HashingEmbedder()


def build_embedder(settings: Settings) -> Embedder:
    return _embedder_for(settings)


@dataclass(frozen=True, slots=True)
class MemoryDocument:
    doc_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryStore(ABC):
    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def index_document(self, doc: MemoryDocument, embedding: list[float] | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def hybrid_search(
        self, query: str, *, top_k: int = 5, embedder: Embedder | None = None
    ) -> list[MemoryHit]:
        raise NotImplementedError

    @abstractmethod
    async def snapshot(self, run_id: str, step_index: int, blob: dict[str, Any]) -> str:
        """Persist a time-travel point; returns an opaque ref."""
        raise NotImplementedError

    @abstractmethod
    async def get_snapshot(self, ref: str) -> dict[str, Any]:
        raise NotImplementedError


class InMemoryMemoryStore(MemoryStore):
    """Dependency-free store: term-frequency BM25 + token-overlap fusion."""

    def __init__(self) -> None:
        self._docs: dict[str, MemoryDocument] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def index_document(self, doc: MemoryDocument, embedding: list[float] | None = None) -> None:
        self._docs[doc.doc_id] = doc

    async def hybrid_search(
        self, query: str, *, top_k: int = 5, embedder: Embedder | None = None
    ) -> list[MemoryHit]:
        query_terms = set(_tokenize(query))
        scored: list[MemoryHit] = []
        for doc_id, doc in self._docs.items():
            doc_terms = _tokenize(doc.text)
            overlap = len(query_terms & set(doc_terms))
            score = 0.0
            if doc_terms:
                score += overlap / math.sqrt(len(doc_terms))
            scored.append(MemoryHit(id=doc_id, text=doc.text, score=score, metadata=doc.metadata))
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:top_k]

    async def snapshot(self, run_id: str, step_index: int, blob: dict[str, Any]) -> str:
        ref = f"{run_id}:{step_index}:{uuid4().hex[:8]}"
        self._snapshots[ref] = {"run_id": run_id, "step_index": step_index, **blob}
        return ref

    async def get_snapshot(self, ref: str) -> dict[str, Any]:
        try:
            return self._snapshots[ref]
        except KeyError as exc:
            raise KeyError(f"snapshot not found: {ref}") from exc


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+", text.lower())


class PostgresHybridMemory(MemoryStore):
    """Production store: pgvector cosine similarity + tsvector BM25, RRF-fused.

    Uses plain parameterized SQL against asyncpg; the schema is created
    idempotently on `start`.
    """

    TABLE = "agent_memory"

    def __init__(self, settings: Settings) -> None:
        self._url = settings.database_url
        self._pool: Any = None

    async def start(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(self._url, min_size=2, max_size=10)
        await self._create_schema()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _create_schema(self) -> None:
        assert self._pool is not None
        await self._pool.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await self._pool.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE} (
                doc_id        TEXT PRIMARY KEY,
                text          TEXT NOT NULL,
                embedding     vector(1536),
                tokens        tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
                metadata      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

    async def index_document(self, doc: MemoryDocument, embedding: list[float] | None = None) -> None:
        assert self._pool is not None
        embedding = embedding or [0.0] * 1536
        await self._pool.execute(
            f"""
            INSERT INTO {self.TABLE} (doc_id, text, embedding, metadata)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (doc_id) DO UPDATE
              SET text = EXCLUDED.text,
                  embedding = EXCLUDED.embedding,
                  metadata = EXCLUDED.metadata
            """,
            doc.doc_id,
            doc.text,
            embedding,
            doc.metadata,
        )

    async def hybrid_search(
        self, query: str, *, top_k: int = 5, embedder: Embedder | None = None
    ) -> list[MemoryHit]:
        assert self._pool is not None
        embedder = embedder or HashingEmbedder()
        query_embedding = await embedder.embed(query)
        vector_literal = "[" + ",".join(repr(float(v)) for v in query_embedding) + "]"
        k = max(top_k * 3, 10)  # candidate pool before RRF fusion

        lexical_rows = await self._pool.fetch(
            f"""
            SELECT doc_id, text, metadata
            FROM {self.TABLE}
            WHERE tokens @@ plainto_tsquery('english', $1)
            LIMIT $2
            """,
            query,
            k,
        )
        semantic_rows = await self._pool.fetch(
            f"""
            SELECT doc_id, text, metadata
            FROM {self.TABLE}
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $2
            """,
            vector_literal,
            k,
        )

        # Reciprocal Rank Fusion of the two independent rankings.
        rrf_scores: dict[str, float] = {}
        candidates: dict[str, MemoryHit] = {}
        for rank, rows in enumerate((lexical_rows, semantic_rows)):
            for position, row in enumerate(rows):
                doc_id = row["doc_id"]
                rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + 1.0 / (60 + position + rank)
                candidates[doc_id] = MemoryHit(
                    id=doc_id,
                    text=row["text"],
                    score=rrf_scores[doc_id],
                    metadata=dict(row["metadata"]) if isinstance(row["metadata"], dict) else {},
                )

        winners = sorted(candidates.values(), key=lambda hit: hit.score, reverse=True)[:top_k]
        for winner in winners:
            winner.metadata["rrf"] = winner.score
        return winners

    async def snapshot(self, run_id: str, step_index: int, blob: dict[str, Any]) -> str:
        ref = f"{run_id}:{step_index}:{uuid4().hex[:8]}"
        blob = {"run_id": run_id, "step_index": step_index, **blob}
        await self.index_document(
            MemoryDocument(doc_id=ref, text=json_dumps(blob), metadata={"kind": "snapshot", "run_id": run_id})
        )
        return ref

    async def get_snapshot(self, ref: str) -> dict[str, Any]:
        assert self._pool is not None
        row = await self._pool.fetchrow(f"SELECT text FROM {self.TABLE} WHERE doc_id = $1", ref)
        if row is None:
            raise KeyError(f"snapshot not found: {ref}")
        return json_loads(row["text"])


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def json_loads(value: str) -> dict[str, Any]:
    import json

    return cast(dict[str, Any], json.loads(value))


def build_memory(settings: Settings) -> MemoryStore:
    if settings.is_test:
        return InMemoryMemoryStore()
    return PostgresHybridMemory(settings)
