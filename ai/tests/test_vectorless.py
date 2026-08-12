"""Vectorless RAG (SQLite + PageIndex) tests."""

from ai.rag.chunking import Chunk
from ai.rag.vectorless import PageIndex


def _doc(text: str) -> list[Chunk]:
    return [Chunk(text=text, index=0, token_count=len(text) // 4, source="t")]


def test_empty_index_returns_no_hits():
    idx = PageIndex(":memory:")
    assert idx.search("disk full") == []
    idx.close()


def test_add_and_search_term_overlap():
    idx = PageIndex(":memory:")
    idx.add("a", _doc("disk full: clean /var/log and rotate old logs"))
    idx.add("b", _doc("cpu saturation: restart the noisy worker"))
    hits = idx.search("disk full", top_k=5)
    assert hits
    assert hits[0].doc_id == "a"
    idx.close()


def test_ranked_by_query_terms():
    idx = PageIndex(":memory:")
    idx.add("a", _doc("disk full disk full disk full disk full"))
    idx.add("b", _doc("disk full once, cpu fine"))
    hits = idx.search("disk full", top_k=5)
    assert hits[0].doc_id == "a"
    idx.close()


def test_clear_removes_pages():
    idx = PageIndex(":memory:")
    idx.add("a", _doc("disk full mitigation"))
    assert len(idx) == 1
    idx.clear()
    assert len(idx) == 0
    assert idx.search("disk") == []
    idx.close()


def test_persistent_file_db(tmp_path):
    db = tmp_path / "idx.db"
    idx = PageIndex(str(db))
    idx.add("a", _doc("verify with df -h after cleanup"))
    hits = idx.search("df -h", top_k=3)
    assert hits and hits[0].doc_id == "a"
    idx.close()
