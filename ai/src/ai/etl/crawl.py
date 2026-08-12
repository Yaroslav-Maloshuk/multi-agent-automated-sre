"""Crawl4AI ingestion — crawl documentation/wiki pages into markdown text."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class CrawledDoc:
    doc_id: str
    url: str
    title: str
    markdown: str
    links: list[str] = field(default_factory=list)

    @classmethod
    def from_markdown(cls, url: str, title: str, markdown: str) -> CrawledDoc:
        doc_id = hashlib.sha1(f"{url}|{title}".encode()).hexdigest()[:16]
        return cls(doc_id=doc_id, url=url, title=title, markdown=markdown)


def seed_docs() -> list[CrawledDoc]:
    """Offline seed corpus used when Crawl4AI/network is unavailable (tests, demos)."""
    return [
        CrawledDoc.from_markdown(
            "https://example.sre.dev/runbooks/disk-full",
            "Disk full runbook",
            """When a node is disk full, alert on_filesystem_space fires.
            Root cause is usually failed log rotation or oversized artifacts.
            Mitigation: clean /var/log/*.gz older than 7 days, then re-check df.
            After mitigation, verify with df -h and notify on-call.""",
        ),
        CrawledDoc.from_markdown(
            "https://example.sre.dev/runbooks/cpu-saturation",
            "CPU saturation runbook",
            """CPU saturation is identified by load1 exceeding 4x cores.
            Common causes: leaking goroutines, unbounded retry loops, GC pressure.
            Mitigation: sample /proc/*/stat, restart the noisy worker, tune HPA.
            Verify with load averages over 15 minutes.""",
        ),
    ]


async def crawl(url: str, *, max_pages: int = 5) -> list[CrawledDoc]:
    """Crawl a site via Crawl4AI (async). Falls back to seed docs on ImportError."""
    try:
        from crawl4ai import AsyncWebCrawler  # type: ignore

        docs: list[CrawledDoc] = []
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url)
            if result and result.markdown:
                title = url.rstrip("/").rsplit("/", 1)[-1]
                docs.append(CrawledDoc.from_markdown(url, title, result.markdown))
        return docs[:max_pages]
    except ImportError:
        return seed_docs()[:max_pages]
