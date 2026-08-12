"""Collector runner: periodic poll → normalize → create incidents.

Deduplicates by fingerprint across poll cycles so an alert that stays "firing"
does not flood the command center with duplicate incidents.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.collectors.base import AlertCollector, NormalizedAlert
from app.collectors.datadog import DatadogCollector
from app.collectors.kubernetes import KubernetesCollector
from app.collectors.prometheus import PrometheusCollector
from app.core.config import Settings

logger = logging.getLogger(__name__)


def build_collectors(settings: Settings) -> list[AlertCollector]:
    enabled = {name.strip().lower() for name in settings.collectors_enabled.split(",") if name.strip()}
    if not enabled:
        return []
    candidates: list[AlertCollector] = [
        DatadogCollector(settings),
        PrometheusCollector(settings),
        KubernetesCollector(settings),
    ]
    return [c for c in candidates if c.name in enabled and c.enabled]


class CollectorRunner:
    """Asyncio loop that polls enabled collectors and ingests alerts."""

    def __init__(self, settings: Settings, create: Any) -> None:
        self._settings = settings
        self._create = create
        self._collectors = build_collectors(settings)
        self._seen: dict[str, float] = {}
        self._task: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        return bool(self._collectors)

    def summary(self) -> list[dict[str, Any]]:
        return [{"name": c.name, "enabled": True} for c in self._collectors]

    async def start(self) -> None:
        if not self._collectors or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="collector-runner")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def ingest(self, alerts: list[NormalizedAlert]) -> int:
        created = 0
        for alert in alerts:
            if not self._dedupe(alert):
                continue
            await self._create(alert.to_incident())
            created += 1
        return created

    def _dedupe(self, alert: NormalizedAlert) -> bool:
        now = time.monotonic()
        key = alert.fingerprint()
        window = max(2, int(self._settings.collector_poll_interval)) * 3
        if key in self._seen and now - self._seen[key] < window:
            return False
        self._seen[key] = now
        return True

    async def _loop(self) -> None:
        interval = max(5, int(self._settings.collector_poll_interval))
        logger.info("collector runner started: %s", [c.name for c in self._collectors])
        while True:
            for collector in self._collectors:
                try:
                    alerts = await collector.check()
                    if alerts:
                        created = await self.ingest(alerts)
                        if created:
                            logger.info("collector %s ingested %d alert(s)", collector.name, created)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("collector %s check failed: %s", collector.name, exc)
            await asyncio.sleep(interval)
