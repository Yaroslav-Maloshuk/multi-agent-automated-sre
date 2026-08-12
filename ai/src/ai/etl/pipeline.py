"""ETL pipeline: Crawl4AI -> Chonkie chunk -> embed -> PageIndex/vector stores
-> DuckDB warehouse. `run()` is the single entry point; every step degrades
gracefully when optional stores are offline.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..rag import PageIndex, chunk
from ..routing import embed
from .crawl import CrawledDoc, seed_docs
from .duck import Warehouse


@dataclass
class ETLResult:
    docs: int
    chunks: int
    tokens: int
    indexed_stores: list[str]


async def run(
    *,
    urls: list[str] | None = None,
    page_index: PageIndex | None = None,
    warehouse: Warehouse | None = None,
    qdrant_collection: str | None = None,
    lancedb_path: str | None = None,
    max_pages: int = 5,
) -> ETLResult:
    """Ingest docs (crawl or seed), chunk, embed, index, and record to DuckDB."""
    docs: list[CrawledDoc] = []
    if urls:
        for url in urls[:max_pages]:
            docs.extend(await _crawl(url, max_pages=max_pages))
    else:
        docs = seed_docs()

    total_chunks = 0
    total_tokens = 0
    indexed_stores: list[str] = []

    if warehouse is None:
        warehouse = Warehouse()

    for doc in docs:
        warehouse.record_doc(doc.doc_id, doc.url, doc.url)
        chunks = chunk(doc.markdown, max_tokens=512, source=doc.url)
        total_chunks += len(chunks)
        total_tokens += sum(c.token_count for c in chunks)

        if page_index is not None:
            page_index.add(doc.doc_id, chunks)
            indexed_stores.append("pageindex")

        for c in chunks:
            warehouse.record_chunk(doc.doc_id, c.index, c.token_count, c.text)

        if lancedb_path or qdrant_collection:
            vectors = await embed([c.text for c in chunks])
            if qdrant_collection:
                _index_qdrant(qdrant_collection, chunks, vectors, doc.doc_id)
                indexed_stores.append("qdrant")
            if lancedb_path:
                _index_lance(lancedb_path, chunks, vectors, doc.doc_id)
                indexed_stores.append("lancedb")

    warehouse.record_roi("etl_tokens", float(total_tokens))
    return ETLResult(
        docs=len(docs),
        chunks=total_chunks,
        tokens=total_tokens,
        indexed_stores=sorted(set(indexed_stores)),
    )


async def _crawl(url: str, *, max_pages: int) -> list[CrawledDoc]:
    from .crawl import crawl

    return await crawl(url, max_pages=max_pages)


def _index_qdrant(collection: str, chunks: list, vectors: list[list[float]], doc_id: str) -> None:
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, PointStruct, VectorParams

        client = QdrantClient(url="http://localhost:6333", timeout=1.0)
        if collection not in {c.name for c in client.get_collections().collections}:
            client.create_collection(
                collection,
                vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE),
            )
        client.upsert(
            collection_name=collection,
            points=[
                PointStruct(
                    id=i,
                    vector=vec,
                    payload={"text": c.text, "doc_id": doc_id, "index": c.index},
                )
                for i, (c, vec) in enumerate(zip(chunks, vectors, strict=True))
            ],
        )
    except Exception:  # noqa: BLE001 - Qdrant offline; skip silently
        pass


def _index_lance(path: str, chunks: list, vectors: list[list[float]], doc_id: str) -> None:
    try:
        import lancedb

        db = lancedb.connect(path)
        import pyarrow as pa

        schema = pa.schema(
            [
                pa.field("id", pa.int32()),
                pa.field("doc_id", pa.string()),
                pa.field("text", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), len(vectors[0]))),
            ]
        )
        table = db.create_table(
            "chunks",
            data=[
                {"id": i, "doc_id": doc_id, "text": c.text, "vector": vec}
                for i, (c, vec) in enumerate(zip(chunks, vectors, strict=True))
            ],
            schema=schema,
            mode="overwrite",
        )
        del table
    except Exception:  # noqa: BLE001 - LanceDB offline; skip silently
        pass
