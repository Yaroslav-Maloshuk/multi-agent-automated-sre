"""Chonkie chunking contract tests (offline fallback path)."""

from ai.rag.chunking import Chunk, chunk


def test_chunk_splits_long_document():
    text = "word " * 300
    chunks = chunk(text, max_tokens=64, source="doc")
    assert len(chunks) > 1
    assert all(isinstance(c, Chunk) for c in chunks)
    assert all(c.index == i for i, c in enumerate(chunks))


def test_chunk_never_returns_empty():
    assert chunk("") == []
    text = "A single short sentence."
    out = chunk(text)
    assert out and out[0].text == text


def test_chunk_token_count_positive():
    for c in chunk("a b c d e f g h", max_tokens=8):
        assert c.token_count > 0
