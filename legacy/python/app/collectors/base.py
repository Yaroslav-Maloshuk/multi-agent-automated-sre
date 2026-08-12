"""Alert collector contracts.

`AlertCollector.check()` returns zero or more `NormalizedAlert`s. Pollers
(Datadog API, Prometheus ALERTS query, Kubernetes events) are driven by
`CollectorRunner`; webhook endpoints (routes/collectors.py) reuse the same
normalizers for push-style ingestion.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NormalizedAlert:
    """Source-agnostic alert, ready to become an incident."""

    source: str
    title: str
    severity: str = "high"
    service: str | None = None
    error: str | None = None
    message: str | None = None
    target_file: str | None = None
    files: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Stable identity for dedupe across poll cycles."""
        return f"{self.source}:{self.title}:{self.service or ''}"

    def to_incident(self, *, auto_run: bool = True) -> dict[str, Any]:
        return {
            "title": self.title[:300],
            "severity": self.severity,
            "source": self.source,
            "service": self.service,
            "error": self.error,
            "message": self.message,
            "target_file": self.target_file,
            "files": self.files,
            "raw_payload": self.raw,
            "auto_run": auto_run,
        }


class AlertCollector(ABC):
    """One native alerting source. `enabled` gates the runner."""

    name: str = "abstract"

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return False

    @abstractmethod
    async def check(self) -> list[NormalizedAlert]:
        """Pull the current alert set from the upstream system."""
        raise NotImplementedError


def normalize_severity(value: str | None, *, fallback: str = "high") -> str:
    """Map arbitrary severity vocabularies to our low|medium|high|critical."""
    if not value:
        return fallback
    v = value.strip().lower()
    if v in {"critical", "criticality=critical", "p1", "sev1", "error", "fatal", "page", "pageable"}:
        return "critical"
    if v in {"high", "p2", "sev2", "warning", "major", "urgent", "alert"}:
        return "high"
    if v in {"medium", "p3", "sev3", "info", "informational", "minor", "warning_only"}:
        return "medium"
    if v in {"low", "p4", "p5", "sev4", "sev5", "none", "debug"}:
        return "low"
    return fallback
