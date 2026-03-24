# -*- coding: utf-8 -*-
"""Heartbeat monitor — two-layer LLM architecture for task anomaly detection.

Layer 1 (Triage): Lightweight model judges task snapshot — normal or anomaly.
Layer 2 (Action): Stronger model generates human-friendly notification on anomaly.

Notifications delivered to user DM via dispatcher, with 30-min dedup window.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Coroutine
from zoneinfo import ZoneInfo

from core.types import Message

log = logging.getLogger("agentic.jobs.heartbeat")

# -- Constants ----------------------------------------------------------------

HEARTBEAT_TOKEN = "HEARTBEAT_OK"
HEARTBEAT_TOKEN_PATTERN = re.compile(
    r"(\*{0,2})HEARTBEAT_OK(\*{0,2})",
    re.IGNORECASE,
)
ACK_MAX_CHARS = 300
SNAPSHOT_TIMEOUT = 30  # seconds for task_ctl.py snapshot subprocess

TRIAGE_PROMPT = (
    "当前时间：{current_time}\n\n"
    "以下是当前任务状态快照：\n\n"
    "{task_snapshot}\n\n"
    "判断规则：\n"
    "- 已逾期（截止时间 < 当前时间）→ 异常\n"
    "- 距离截止不足 30 分钟 → 异常\n"
    "- 所有任务截止时间充裕 → 正常\n\n"
    "如果正常，回复 HEARTBEAT_OK。\n"
    "如果异常，简要列出需要处理的事项（哪个任务、什么状态、距离截止多久）。"
)

ACTION_PROMPT = (
    "你是用户的 AI 助手，通过飞书 DM 给用户发提醒。\n\n"
    "需要关注的任务：\n{triage_findings}\n\n"
    "任务快照：\n{task_snapshot}\n\n"
    "**输出规则（严格遵守）：**\n"
    "- 只输出最终发给用户的消息文本\n"
    "- 禁止输出分析过程、内部推理、行动计划\n"
    "- 风格：像朋友提醒一样自然简短，一两句话\n"
    "- 禁止使用「心跳」「行动报告」「任务状态确认」等系统术语\n"
    "- 示例：「XX 快到期了，12:30 截止，要不要帮你标记完成？」"
)


# -- Health status (backward compat) ------------------------------------------

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
    """Two-layer heartbeat: triage (cheap LLM) -> action (stronger LLM) -> notify.

    Usage:
        monitor = HeartbeatMonitor(
            usage_tracker=tracker,
            daily_budget_usd=50.0,
            notify_fn=dispatcher.send_text,
            notify_chat_id="user_dm_chat_id",
            triage_provider=gemini_flash_provider,
            action_provider=gemini_flash_provider,
            workspace_dir="/path/to/workspace",
        )
        # Register as a scheduler job with interval=300 (5 min)
    """

    def __init__(
        self,
        usage_tracker: Any = None,
        daily_budget_usd: float = 5.0,
        notify_fn: Callable[..., Coroutine] | None = None,
        notify_chat_id: str = "",
        triage_provider: Any = None,
        action_provider: Any = None,
        workspace_dir: str = "",
        active_hours: tuple[str, str] = ("00:00", "23:59"),
        timezone: str = "Asia/Shanghai",
        alert_window_hours: int = 2,
        explorer: Any = None,
        idle_window_minutes: int = 30,
    ):
        self._usage = usage_tracker
        self._budget = daily_budget_usd
        self._notify = notify_fn
        self._chat_id = notify_chat_id
        self._triage_provider = triage_provider
        self._action_provider = action_provider
        self._workspace_dir = workspace_dir
        self._active_start = active_hours[0]
        self._active_end = active_hours[1]
        self._tz_name = timezone
        self._alert_window_hours = alert_window_hours
        self._history: list[HealthStatus] = []

        # Notification dedup: hash -> timestamp
        self._sent_hashes: dict[str, float] = {}
        self._dedup_window = 1800  # 30 minutes

        # Explorer idle trigger
        self._explorer = explorer
        self._idle_window = idle_window_minutes * 60
        self._last_user_activity: float = time.time()

    async def check(self) -> HealthStatus:
        """Run health checks -- budget + two-layer task triage."""
        status = HealthStatus()

        # -- Cost check --
        if self._usage:
            status.daily_cost_usd = self._usage.daily_total()
            if self._usage.check_budget(self._budget):
                status.anomalies.append(
                    f"日消耗 ${status.daily_cost_usd:.2f} 超预算 ${self._budget:.2f}"
                )

        # -- Two-layer task triage --
        if self._triage_provider and self._is_within_active_hours():
            try:
                result = await self._run_triage()
                if result == "anomaly_notified":
                    status.anomalies.append("task_anomaly_notified")
            except Exception:
                log.exception("Heartbeat triage failed")

        self._history.append(status)

        # -- Notify budget anomalies (legacy path) --
        budget_anomalies = [a for a in status.anomalies if a != "task_anomaly_notified"]
        if budget_anomalies and self._notify and self._chat_id:
            msg = "⚠️ 系统告警\n" + "\n".join(f"- {a}" for a in budget_anomalies)
            try:
                await self._notify(self._chat_id, msg)
            except Exception:
                log.warning("Heartbeat budget notification failed")

        return status

    async def _run_triage(self) -> str:
        """Two-layer triage. Returns 'ok', 'anomaly_notified', or 'skipped'."""

        # -- Collect task snapshot --
        snapshot = await self._collect_task_snapshot()
        if not snapshot:
            log.debug("Heartbeat skipped: no task data")
            return "skipped"

        now = datetime.now(ZoneInfo(self._tz_name))
        current_time = now.strftime("%Y-%m-%d %H:%M (%A)")

        # -- Layer 1: Triage (cheap model) --
        triage_prompt = TRIAGE_PROMPT.format(
            current_time=current_time,
            task_snapshot=snapshot,
        )
        triage_msg = Message(role="user", content=triage_prompt)

        triage_response = await self._triage_provider.chat(
            messages=[triage_msg],
            stream=False,
            max_tokens=1024,
        )
        triage_text = (
            triage_response.text
            if hasattr(triage_response, "text")
            else str(triage_response.content)
        )

        should_skip, cleaned = self._strip_heartbeat_token(triage_text)
        if should_skip:
            log.info("Heartbeat OK (triage: all clear)")

            # Idle exploration trigger: if triage is OK and system has been
            # idle for idle_window, start an explorer session in background
            await self._maybe_trigger_exploration()

            return "ok"

        log.info("Heartbeat triage detected anomaly, triggering action layer")

        # -- Layer 2: Action (stronger model) --
        action_text = cleaned  # fallback if action provider unavailable
        if self._action_provider:
            action_prompt = ACTION_PROMPT.format(
                triage_findings=cleaned,
                task_snapshot=snapshot,
            )
            action_msg = Message(role="user", content=action_prompt)

            try:
                action_response = await self._action_provider.chat(
                    messages=[action_msg],
                    stream=False,
                    max_tokens=512,
                )
                action_text = (
                    action_response.text
                    if hasattr(action_response, "text")
                    else str(action_response.content)
                )
            except Exception:
                log.warning("Heartbeat action LLM failed, using triage findings")

        # -- Deliver notification --
        ok = await self._deliver(action_text)
        if ok:
            log.info("Heartbeat action delivered (%d chars)", len(action_text))
            return "anomaly_notified"
        return "skipped"

    # -- Helpers --

    def _is_duplicate(self, text: str) -> bool:
        """Check if similar notification was sent within dedup window."""
        now = time.time()
        self._sent_hashes = {
            h: ts for h, ts in self._sent_hashes.items()
            if now - ts < self._dedup_window
        }
        h = hashlib.md5(text.encode()).hexdigest()
        if h in self._sent_hashes:
            return True
        self._sent_hashes[h] = now
        return False

    async def _deliver(self, text: str) -> bool:
        """Send notification via notify_fn, with dedup."""
        if self._is_duplicate(text):
            log.info("Heartbeat notification suppressed (duplicate)")
            return False
        if self._notify and self._chat_id:
            try:
                await self._notify(self._chat_id, text)
                return True
            except Exception:
                log.warning("Heartbeat notification delivery failed")
                return False
        log.debug("Heartbeat: no notify_fn or chat_id, skipping delivery")
        return False

    def _is_within_active_hours(self) -> bool:
        now = datetime.now(ZoneInfo(self._tz_name))
        now_minutes = now.hour * 60 + now.minute
        start = self._parse_hhmm(self._active_start)
        end = self._parse_hhmm(self._active_end)
        if end > start:
            return start <= now_minutes < end
        else:
            return now_minutes >= start or now_minutes < end

    @staticmethod
    def _parse_hhmm(s: str) -> int:
        parts = s.split(":")
        return int(parts[0]) * 60 + int(parts[1])

    @staticmethod
    def _strip_heartbeat_token(text: str) -> tuple[bool, str]:
        """Strip HEARTBEAT_OK token. Returns (should_skip, cleaned_text)."""
        has_token = bool(HEARTBEAT_TOKEN_PATTERN.search(text))
        cleaned = HEARTBEAT_TOKEN_PATTERN.sub("", text).strip()
        if has_token and len(cleaned) <= ACK_MAX_CHARS:
            return True, cleaned
        return False, cleaned

    async def _collect_task_snapshot(self) -> str:
        """Collect task snapshot via task_ctl.py subprocess."""
        if not self._workspace_dir:
            return ""
        script = os.path.join(
            self._workspace_dir,
            ".claude/skills/feishu-task/scripts/task_ctl.py",
        )
        if not os.path.exists(script):
            return ""
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", script, "snapshot",
                "--window-hours", str(self._alert_window_hours),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._workspace_dir,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=SNAPSHOT_TIMEOUT,
            )
            return stdout.decode("utf-8").strip()
        except Exception as e:
            log.warning("Task snapshot failed: %s", e)
            return ""

    async def _maybe_trigger_exploration(self) -> None:
        """Trigger explorer session if system is idle and queue has tasks."""
        if not self._explorer:
            return
        if self._explorer.is_running:
            return

        # Check if we're in a good time window (e.g., late night / early morning)
        now = datetime.now(ZoneInfo(self._tz_name))
        hour = now.hour
        # Only trigger during off-hours (23:00 - 07:00)
        if 7 <= hour < 23:
            return

        # Check queue has pending tasks
        pending = self._explorer._queue.list_items(status="queued")
        if not pending:
            return

        log.info("Idle exploration triggered: %d pending tasks, hour=%d",
                 len(pending), hour)
        asyncio.create_task(self._explorer.run_session())

    def get_history(self, count: int = 10) -> list[HealthStatus]:
        return self._history[-count:]
