"""Chunking layer (Chonkie) with a dependency-free fallback.

Keeps a stable `chunk(text) -> list[Chunk]` contract so the ETL pipeline and
tests are framework-agnostic. Chonkie provides the token-aware Semantic/
Recursive splitters in production; the fallback is a greedy character splitter
with soft sentence boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec

_HAS_CHONKIE = find_spec("chonkie") is not None


@dataclass
class Chunk:
    text: str
    index: int
    token_count: int
    source: str


def chunk(text: str, *, max_tokens: int = 512, source: str = "doc") -> list[Chunk]:
    """Split `text` into overlapping chunks. Core contract used by ETL + RAG."""
    if _HAS_CHONKIE:
        try:
            return _chunk_chonkie(text, max_tokens=max_tokens, source=source)
        except Exception:  # noqa: BLE001 - never break ETL on chunker failure
            pass
    return _chunk_fallback(text, max_tokens=max_tokens, source=source)


def _chunk_chonkie(text: str, *, max_tokens: int, source: str) -> list[Chunk]:
    from chonkie import RecursiveChunker

    chunker = RecursiveChunker(chunk_size=max_tokens, chunk_overlap=max_tokens // 8)
    out: list[Chunk] = []
    for i, c in enumerate(chunker.chunk(text)):
        out.append(Chunk(text=c.text, index=i, token_count=_approx_tokens(c.text), source=source))
    return out


def _chunk_fallback(text: str, *, max_tokens: int, source: str) -> list[Chunk]:
    """Greedy sentence-ish splitter; ~4 chars/token approximation."""
    if not text or not text.strip():
        return []
    size = max_tokens * 4
    chunks: list[Chunk] = []
    start = 0
    idx = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            for boundary in (". ", ".\n", "\n\n", " "):
                cut = text.rfind(boundary, start + size // 2, end)
                if cut != -1:
                    end = cut + len(boundary)
                    break
        piece = text[start:end].strip()
        if piece:
            chunks.append(Chunk(text=piece, index=idx, token_count=_approx_tokens(piece), source=source))
            idx += 1
        start = end
    return chunks or [Chunk(text=text, index=0, token_count=_approx_tokens(text), source=source)]


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)
