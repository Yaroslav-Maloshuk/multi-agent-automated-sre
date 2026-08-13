"""Document ingestion + DuckDB warehouse tests (offline corpus)."""

import pytest

from ai.etl import Warehouse, index_text_doc
from ai.rag import PageIndex, chunk


def test_warehouse_bootstrap_and_writes():
    wh = Warehouse()
    wh.record_doc("d1", "runbook", "https://x/disk")
    wh.record_chunk("d1", 0, 64, "clean /var/log")
    wh.record_retrieval("disk full", 5, 3, "hybrid")
    wh.record_roi("etl_tokens", 128.0)

    stats = wh.chunk_stats()
    assert stats["count"] == 1
    assert stats["total_tokens"] == 64
    assert wh.retrieval_recall(min_hits=1) == 1.0
    assert wh.recent_retrievals(limit=5)
    wh.close()


def test_warehouse_recall_zero_when_no_events():
    wh = Warehouse()
    assert wh.retrieval_recall() == 0.0
    wh.close()


@pytest.mark.asyncio
async def test_ingest_indexes_pageindex_and_records_duckdb():
    idx = PageIndex(":memory:")
    wh = Warehouse()
    result = await index_text_doc(
        "d1",
        "disk full runbook: clean /var/log and rotate logs",
        page_index=idx,
        warehouse=wh,
    )
    assert result.docs == 1
    assert result.chunks >= 1
    assert result.tokens > 0
    assert "pageindex" in result.indexed_stores
    assert len(idx) == result.chunks
    assert wh.chunk_stats()["count"] == result.chunks
    hits = idx.search("disk full", top_k=3)
    assert hits
    wh.close()
    idx.close()


def test_chunk_roundtrip_into_pageindex():
    idx = PageIndex(":memory:")
    chunks = chunk("when a node runs out of disk, alert fires", max_tokens=32)
    idx.add("doc1", chunks)
    pages = idx.search("node out of disk")
    assert pages
    assert "disk" in pages[0].text.lower()
    idx.close()
