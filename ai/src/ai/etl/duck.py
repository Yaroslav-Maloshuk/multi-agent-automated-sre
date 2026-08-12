"""DuckDB analytics warehouse.

The ETL pipeline appends every ingested document, chunk and retrieval event to
DuckDB (Parquet-backed) so the workspace can answer analytics questions with
columnar SQL — e.g. token spend, retrieval recall, incident MTTR trends —
without touching OLTP Postgres.
"""

from __future__ import annotations

from pathlib import Path

import duckdb


class Warehouse:
    """Thin DuckDB wrapper with idempotent schema bootstrap."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if str(path) != ":memory:":
            Path(str(path)).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(self.path)
        self._bootstrap()

    def _bootstrap(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS ingested_docs "
            "(doc_id VARCHAR, source VARCHAR, url VARCHAR, ingested_at TIMESTAMP)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks (doc_id VARCHAR, chunk_idx INTEGER, token_count INTEGER, text VARCHAR)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS retrieval_events "
            "(ts TIMESTAMP, query VARCHAR, top_k INTEGER, hits INTEGER, store VARCHAR)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS roi_events (ts TIMESTAMP, metric VARCHAR, value DOUBLE, label VARCHAR)"
        )

    # -- writes -----------------------------------------------------------
    def record_doc(self, doc_id: str, source: str, url: str) -> None:
        self.conn.execute(
            "INSERT INTO ingested_docs VALUES (?, ?, ?, now())", [doc_id, source, url]
        )

    def record_chunk(self, doc_id: str, idx: int, tokens: int, text: str) -> None:
        self.conn.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?)", [doc_id, idx, tokens, text]
        )

    def record_retrieval(self, query: str, top_k: int, hits: int, store: str) -> None:
        self.conn.execute(
            "INSERT INTO retrieval_events VALUES (now(), ?, ?, ?, ?)", [query, top_k, hits, store]
        )

    def record_roi(self, metric: str, value: float, label: str = "") -> None:
        self.conn.execute(
            "INSERT INTO roi_events VALUES (now(), ?, ?, ?)", [metric, value, label]
        )

    # -- queries ----------------------------------------------------------
    def retrieval_recall(self, min_hits: int = 1) -> float:
        total = self.conn.execute("SELECT count(*) FROM retrieval_events").fetchone()[0]
        if total == 0:
            return 0.0
        good = self.conn.execute(
            "SELECT count(*) FROM retrieval_events WHERE hits >= ?", [min_hits]
        ).fetchone()[0]
        return good / total

    def chunk_stats(self) -> dict:
        row = self.conn.execute(
            "SELECT count(*), coalesce(avg(token_count), 0), coalesce(sum(token_count), 0) FROM chunks"
        ).fetchone()
        return {"count": int(row[0]), "avg_tokens": round(float(row[1]), 1), "total_tokens": int(row[2])}

    def recent_retrievals(self, limit: int = 20) -> list[dict]:
        return [
            dict(zip(("ts", "query", "top_k", "hits", "store"), r, strict=True))
            for r in self.conn.execute(
                "SELECT * FROM retrieval_events ORDER BY ts DESC LIMIT ?", [limit]
            ).fetchall()
        ]

    def close(self) -> None:
        self.conn.close()
