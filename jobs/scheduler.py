# -*- coding: utf-8 -*-
"""In-process cron scheduler — asyncio-based periodic job execution.

Supports:
- Cron-like scheduling (minute/hour/weekday)
- Named jobs with enable/disable
- Hot-reload via config dict
- Graceful shutdown
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

log = logging.getLogger("agentic.jobs.scheduler")


@dataclass
class JobConfig:
    """Configuration for a scheduled job."""
    name: str
    handler: Callable[..., Coroutine]
    interval_seconds: int = 3600        # run every N seconds
    enabled: bool = True
    run_on_start: bool = False          # run immediately on scheduler start
    last_run: float = 0
    metadata: dict = field(default_factory=dict)


class Scheduler:
    """Asyncio-based job scheduler.

    Usage:
        scheduler = Scheduler()
        scheduler.add_job(JobConfig(name="heartbeat", handler=hb_fn, interval_seconds=300))
        await scheduler.start()
        ...
        await scheduler.stop()
    """

    def __init__(self) -> None:
        self._jobs: dict[str, JobConfig] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    def add_job(self, job: JobConfig) -> None:
        self._jobs[job.name] = job
        log.info("Job registered: %s (interval=%ds, enabled=%s)",
                 job.name, job.interval_seconds, job.enabled)

    def remove_job(self, name: str) -> bool:
        return self._jobs.pop(name, None) is not None

    def enable_job(self, name: str) -> bool:
        job = self._jobs.get(name)
        if job:
            job.enabled = True
            return True
        return False

    def disable_job(self, name: str) -> bool:
        job = self._jobs.get(name)
        if job:
            job.enabled = False
            return True
        return False

    def list_jobs(self) -> list[dict]:
        return [
            {
                "name": j.name,
                "interval": j.interval_seconds,
                "enabled": j.enabled,
                "last_run": j.last_run,
            }
            for j in self._jobs.values()
        ]

    async def start(self) -> None:
        """Start the scheduler loop."""
        self._running = True

        # Run on-start jobs
        for job in self._jobs.values():
            if job.enabled and job.run_on_start:
                await self._run_job(job)

        self._task = asyncio.create_task(self._loop())
        log.info("Scheduler started with %d jobs", len(self._jobs))

    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Scheduler stopped")

    def reload(self, configs: list[dict]) -> None:
        """Hot-reload job configs from a list of dicts.

        Preserves existing handlers — only updates interval/enabled.
        """
        for cfg in configs:
            name = cfg.get("name", "")
            job = self._jobs.get(name)
            if job:
                job.interval_seconds = cfg.get("interval_seconds", job.interval_seconds)
                job.enabled = cfg.get("enabled", job.enabled)
                log.info("Job reloaded: %s (interval=%ds, enabled=%s)",
                         name, job.interval_seconds, job.enabled)

    async def _loop(self) -> None:
        """Main scheduler loop — checks every 30s for due jobs."""
        while self._running:
            await asyncio.sleep(30)
            now = time.time()
            for job in self._jobs.values():
                if not job.enabled:
                    continue
                if now - job.last_run >= job.interval_seconds:
                    asyncio.create_task(self._run_job(job))

    async def _run_job(self, job: JobConfig) -> None:
        """Execute a single job."""
        job.last_run = time.time()
        try:
            await job.handler()
            log.info("Job '%s' completed", job.name)
        except Exception:
            log.exception("Job '%s' failed", job.name)
