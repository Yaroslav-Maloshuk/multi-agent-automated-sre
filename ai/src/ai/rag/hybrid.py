"""Hybrid RAG retrieval — Qdrant (semantic) + LanceDB (vector table) fused
with the SQLite PageIndex (vectorless) via Reciprocal Rank Fusion.

`search()` returns ranked `Hit` objects regardless of which stores are
available, so the gateway works with any subset (or none) of the vector
backends. Each store participates in the RRF only if it is reachable.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..routing import embed

RRF_K = 60


@dataclass
class Hit:
    text: str
    score: float
    source: str
    payload: dict = None  # type: ignore[assignment]


@dataclass
class HybridRetriever:
    page_index: object
    qdrant_collection: str = "sre_snapshots"
    qdrant_url: str = "http://localhost:6333"
    lancedb_path: str = "var/lancedb"

    async def search(self, query: str, *, top_k: int = 5) -> list[Hit]:
        ranked_lists: list[list[Hit]] = []

        vectorless = self._page_hits(query)
        if vectorless:
            ranked_lists.append(vectorless)

        semantic = self._qdrant_hits(query)
        if semantic:
            ranked_lists.append(semantic)

        lance = self._lance_hits(query)
        if lance:
            ranked_lists.append(lance)

        return self._rrf(ranked_lists, top_k)

    # -- vectorless -------------------------------------------------------
    def _page_hits(self, query: str, top_k: int = 5) -> list[Hit]:
        if self.page_index is None:
            return []
        try:
            pages = self.page_index.search(query, top_k=top_k)
        except Exception:  # noqa: BLE001
            return []
        return [Hit(text=p.text, score=1.0 / (RRF_K + i), source="pageindex") for i, p in enumerate(pages)]

    # -- Qdrant semantic --------------------------------------------------
    def _qdrant_hits(self, query: str, top_k: int = 5) -> list[Hit]:
        try:
            from qdrant_client import QdrantClient

            client = QdrantClient(url=self.qdrant_url, timeout=1.0)
            vector = _blocking_embed(query)
            points = client.query_points(
                collection_name=self.qdrant_collection, query=vector, limit=top_k, timeout=1.0
            ).points
            hits = []
            for i, pt in enumerate(points):
                text = pt.payload.get("text", "")
                if text:
                    hits.append(Hit(text=text, score=1.0 / (RRF_K + i), source="qdrant", payload=pt.payload))
            return hits
        except Exception:  # noqa: BLE001 - store offline, skip in fusion
            return []

    # -- LanceDB table ----------------------------------------------------
    def _lance_hits(self, query: str, top_k: int = 5) -> list[Hit]:
        try:
            import lancedb

            db = lancedb.connect(self.lancedb_path)
            if "chunks" not in db.table_names():
                return []
            table = db.open_table("chunks")
            vector = _blocking_embed(query)
            rows = table.search(vector).limit(top_k).to_list()
            return [
                Hit(text=r.get("text", ""), score=1.0 / (RRF_K + i), source="lancedb")
                for i, r in enumerate(rows)
                if r.get("text")
            ]
        except Exception:  # noqa: BLE001 - store offline, skip in fusion
            return []

    # -- fusion -----------------------------------------------------------
    @staticmethod
    def _rrf(ranked_lists: list[list[Hit]], top_k: int) -> list[Hit]:
        fused: dict[str, float] = {}
        source_of: dict[str, str] = {}
        first_of: dict[str, str] = {}
        for ranked in ranked_lists:
            for rank, hit in enumerate(ranked):
                fused[hit.text] = fused.get(hit.text, 0.0) + 1.0 / (RRF_K + rank + 1)
                source_of.setdefault(hit.text, hit.source)
                first_of.setdefault(hit.text, hit.text)
        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        return [
            Hit(text=t, score=s, source=source_of.get(t, "rrf"))
            for t, s in ordered[:top_k]
        ]


def _blocking_embed(text: str) -> list[float]:
    import asyncio

    return asyncio.run(embed([text]))[0]
