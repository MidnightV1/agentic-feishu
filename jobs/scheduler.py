# -*- coding: utf-8 -*-
"""In-process cron scheduler — asyncio-based periodic job execution.

Supports:
- Cron expressions (via croniter): "0 9 * * *"
- Interval scheduling: every N seconds
- Error backoff: exponential delay on consecutive failures
- One-shot jobs: disabled after first run
- Hot-reload via config dict
- Graceful shutdown
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine
from zoneinfo import ZoneInfo

log = logging.getLogger("agentic.jobs.scheduler")

MAX_TIMER_DELAY = 60  # seconds — max sleep between checks
ERROR_BACKOFF = [30, 60, 300, 900, 3600]  # seconds per consecutive error


@dataclass
class JobConfig:
    """Configuration for a scheduled job."""
    name: str
    handler: Callable[..., Coroutine]
    # Schedule: set ONE of these
    interval_seconds: int = 0           # run every N seconds (0 = use cron)
    cron: str = ""                      # cron expression: "*/5 * * * *"
    at_time: str = ""                   # daily at time: "09:00"
    timezone: str = "Asia/Shanghai"
    # Behavior
    enabled: bool = True
    run_on_start: bool = False
    one_shot: bool = False              # disable after first run
    # State (managed internally)
    last_run: float = 0
    next_run_at: float = 0
    consecutive_errors: int = 0
    last_error: str = ""
    metadata: dict = field(default_factory=dict)


def _compute_next_run(job: JobConfig, now: float) -> float:
    """Compute next run time for a job."""
    tz = ZoneInfo(job.timezone)

    if job.cron:
        try:
            from croniter import croniter
            dt_now = datetime.fromtimestamp(now, tz=tz)
            cron = croniter(job.cron, dt_now)
            return cron.get_next(float)
        except Exception as e:
            log.warning("Invalid cron '%s' for job '%s': %s", job.cron, job.name, e)
            return now + 3600  # fallback: 1h

    if job.at_time:
        try:
            h, m = map(int, job.at_time.split(":"))
            dt_now = datetime.fromtimestamp(now, tz=tz)
            target = dt_now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target.timestamp() <= now:
                target = target.replace(day=target.day + 1)
            return target.timestamp()
        except Exception as e:
            log.warning("Invalid at_time '%s' for job '%s': %s", job.at_time, job.name, e)
            return now + 3600

    if job.interval_seconds > 0:
        return now + job.interval_seconds

    return now + 3600  # no schedule configured


class Scheduler:
    """Asyncio-based job scheduler with cron + interval + error backoff.

    Usage:
        scheduler = Scheduler()
        scheduler.add_job(JobConfig(name="heartbeat", handler=fn, interval_seconds=300))
        scheduler.add_job(JobConfig(name="briefing", handler=fn, cron="0 8 * * *"))
        await scheduler.start()
    """

    def __init__(self) -> None:
        self._jobs: dict[str, JobConfig] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    def add_job(self, job: JobConfig) -> None:
        now = time.time()
        if not job.next_run_at:
            if job.run_on_start:
                job.next_run_at = now
            else:
                job.next_run_at = _compute_next_run(job, now)
        self._jobs[job.name] = job
        sched_desc = job.cron or job.at_time or f"{job.interval_seconds}s"
        log.info("Job registered: %s (schedule=%s, enabled=%s)", job.name, sched_desc, job.enabled)

    def remove_job(self, name: str) -> bool:
        return self._jobs.pop(name, None) is not None

    def enable_job(self, name: str) -> bool:
        job = self._jobs.get(name)
        if job:
            job.enabled = True
            if job.next_run_at == 0:
                job.next_run_at = _compute_next_run(job, time.time())
            return True
        return False

    def disable_job(self, name: str) -> bool:
        job = self._jobs.get(name)
        if job:
            job.enabled = False
            return True
        return False

    def list_jobs(self, include_disabled: bool = False) -> list[JobConfig]:
        """Return job configs (for display / #jobs command)."""
        return [
            j for j in self._jobs.values()
            if include_disabled or j.enabled
        ]

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("Scheduler started with %d jobs", len(self._jobs))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Scheduler stopped")

    def reload(self, configs: list[dict]) -> None:
        """Hot-reload job configs — preserves handlers, updates schedule/enabled."""
        for cfg in configs:
            name = cfg.get("name", "")
            job = self._jobs.get(name)
            if job:
                if "interval_seconds" in cfg:
                    job.interval_seconds = cfg["interval_seconds"]
                if "cron" in cfg:
                    job.cron = cfg["cron"]
                if "at_time" in cfg:
                    job.at_time = cfg["at_time"]
                if "enabled" in cfg:
                    job.enabled = cfg["enabled"]
                job.next_run_at = _compute_next_run(job, time.time())
                log.info("Job reloaded: %s", name)

    async def _loop(self) -> None:
        """Main scheduler loop — wakes every MAX_TIMER_DELAY to check due jobs."""
        while self._running:
            now = time.time()
            soonest = now + MAX_TIMER_DELAY

            for job in list(self._jobs.values()):
                if not job.enabled or not job.next_run_at:
                    continue
                if job.next_run_at <= now:
                    # Advance next_run BEFORE execution (crash-safe)
                    job.next_run_at = _compute_next_run(job, now)
                    asyncio.create_task(self._run_job(job))
                else:
                    soonest = min(soonest, job.next_run_at)

            sleep_for = min(MAX_TIMER_DELAY, max(1, soonest - time.time()))
            await asyncio.sleep(sleep_for)

    async def _run_job(self, job: JobConfig) -> None:
        """Execute a single job with error tracking and backoff."""
        job.last_run = time.time()
        try:
            await job.handler()
            job.consecutive_errors = 0
            job.last_error = ""
            log.info("Job '%s' completed", job.name)

            # One-shot: disable after successful run
            if job.one_shot:
                job.enabled = False
                log.info("One-shot job '%s' disabled after completion", job.name)

        except Exception as e:
            job.consecutive_errors += 1
            job.last_error = str(e)[:200]
            log.exception("Job '%s' failed (consecutive_errors=%d)", job.name, job.consecutive_errors)

            # Apply error backoff
            idx = min(job.consecutive_errors - 1, len(ERROR_BACKOFF) - 1)
            backoff = ERROR_BACKOFF[idx]
            job.next_run_at = time.time() + backoff
            log.info("Job '%s' backing off %ds before next attempt", job.name, backoff)
