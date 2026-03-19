# -*- coding: utf-8 -*-
"""Heartbeat monitor — periodic health checks with anomaly detection.

Two-layer architecture:
1. Triage: lightweight check (no tools) — is everything normal?
2. Action: if anomaly detected, deeper investigation with tools

Notifications sent to user DM via dispatcher.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

log = logging.getLogger("agentic.jobs.heartbeat")


@dataclass
class HealthStatus:
    """Snapshot of system health."""
    timestamp: float = field(default_factory=time.time)
    ws_connected: bool = True
    active_sessions: int = 0
    daily_cost_usd: float = 0.0
    pending_tasks: int = 0
    anomalies: list[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return len(self.anomalies) == 0


class HeartbeatMonitor:
    """Periodic health monitor with anomaly notification.

    Usage:
        monitor = HeartbeatMonitor(
            usage_tracker=tracker,
            notify_fn=dispatcher.send_text,
            notify_chat_id="user_dm_chat_id",
        )
        # Register as a scheduler job with interval=300 (5 min)
    """

    def __init__(
        self,
        usage_tracker: Any = None,
        daily_budget_usd: float = 5.0,
        notify_fn: Callable[..., Coroutine] | None = None,
        notify_chat_id: str = "",
    ):
        self._usage = usage_tracker
        self._budget = daily_budget_usd
        self._notify = notify_fn
        self._chat_id = notify_chat_id
        self._history: list[HealthStatus] = []

    async def check(self) -> HealthStatus:
        """Run health checks and return status."""
        status = HealthStatus()

        # Cost check
        if self._usage:
            status.daily_cost_usd = self._usage.daily_total()
            if self._usage.check_budget(self._budget):
                status.anomalies.append(
                    f"日消耗 ${status.daily_cost_usd:.2f} 超预算 ${self._budget:.2f}"
                )

        self._history.append(status)

        # Notify on anomalies
        if status.anomalies and self._notify and self._chat_id:
            msg = "⚠️ 系统告警\n" + "\n".join(f"- {a}" for a in status.anomalies)
            try:
                await self._notify(self._chat_id, msg)
            except Exception:
                log.warning("Heartbeat notification failed")

        return status

    def get_history(self, count: int = 10) -> list[HealthStatus]:
        return self._history[-count:]
