"""SQLite + PageIndex — vectorless RAG.

Implements the PageIndex retrieval scheme: every chunk is a *page* stored in a
SQLite FTS/term table; queries are answered by term-frequency weighted overlap
rather than embedding distance. This is the zero-dependency retrieval path —
no vectors, no torch, no external service — and it works offline.
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .chunking import Chunk

_TOKEN = re.compile(r"[a-z0-9_]+")


def _terms(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in _TOKEN.findall(text.lower()):
        counts[t] = counts.get(t, 0) + 1
    return counts


@dataclass
class Page:
    page_id: int
    doc_id: str
    text: str
    terms: dict[str, int]


class PageIndex:
    """SQLite-backed term-frequency page index with BM25-style scoring."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if str(db_path) != ":memory:":
            Path(str(db_path)).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS pages (
                page_id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT NOT NULL,
                text TEXT NOT NULL
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS page_terms (
                page_id INTEGER NOT NULL,
                term TEXT NOT NULL,
                freq INTEGER NOT NULL,
                PRIMARY KEY (page_id, term)
            )"""
        )
        self._conn.commit()
        self._docs: dict[int, Page] = {}
        self._doc_freq: dict[str, int] = {}
        self._load_pages()

    def _load_pages(self) -> None:
        for page_id, doc_id, text in self._conn.execute("SELECT page_id, doc_id, text FROM pages").fetchall():
            self._docs[int(page_id)] = Page(
                page_id=int(page_id),
                doc_id=doc_id,
                text=text,
                terms=_terms(text),
            )
        self._recompute_doc_freq()

    def add(self, doc_id: str, chunks: list[Chunk]) -> None:
        for chunk in chunks:
            terms = _terms(chunk.text)
            cur = self._conn.execute(
                "INSERT INTO pages (doc_id, text) VALUES (?, ?)", (doc_id, chunk.text)
            )
            page_id = int(cur.lastrowid)
            self._conn.executemany(
                "INSERT OR REPLACE INTO page_terms (page_id, term, freq) VALUES (?, ?, ?)",
                [(page_id, term, freq) for term, freq in terms.items()],
            )
            self._docs[page_id] = Page(page_id=page_id, doc_id=doc_id, text=chunk.text, terms=terms)
        self._conn.commit()
        self._recompute_doc_freq()

    def _recompute_doc_freq(self) -> None:
        rows = self._conn.execute(
            "SELECT term, COUNT(DISTINCT page_id) FROM page_terms GROUP BY term"
        ).fetchall()
        self._doc_freq = {term: int(n) for term, n in rows}

    def search(self, query: str, *, top_k: int = 5) -> list[Page]:
        q_terms = _terms(query)
        if not q_terms or not self._docs:
            return []
        n_pages = max(len(self._docs), 1)
        scored: list[tuple[float, int]] = []
        for page_id, page in self._docs.items():
            length = max(sum(page.terms.values()), 1)
            score = 0.0
            for term, qf in q_terms.items():
                freq = page.terms.get(term, 0)
                if freq == 0:
                    continue
                df = self._doc_freq.get(term, 1)
                idf = math.log((n_pages - df + 0.5) / (df + 0.5) + 1.0)
                tf = freq / (freq + 0.5 + 1.5 * (length / _avg_len(self)))
                score += qf * tf * idf
            scored.append((score, page_id))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [self._docs[pid] for score, pid in scored[:top_k] if score > 0]

    def clear(self) -> None:
        self._conn.execute("DELETE FROM page_terms")
        self._conn.execute("DELETE FROM pages")
        self._conn.commit()
        self._docs.clear()
        self._doc_freq.clear()

    def close(self) -> None:
        self._conn.close()

    def __len__(self) -> int:
        return len(self._docs)


def _avg_len(index: PageIndex) -> float:
    if not index._docs:
        return 1.0
    return sum(sum(p.terms.values()) for p in index._docs.values()) / len(index._docs)
