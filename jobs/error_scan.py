# -*- coding: utf-8 -*-
"""Simplified error scanner — 3-phase pipeline (D5 decision).

Phases:
  1. Parse log file, regex extract ERROR/WARNING, group by signature
  2. LLM classification (cause + fixability: confirm | monitor)
  3. Feishu notification (confirm=orange, monitor=grey)

No auto-fix, no Opus review — simplified from claude-hub 5-phase pipeline.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ── Log parsing ──

_LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(\S+)\s+(ERROR|WARNING)\s+(.+)$"
)

# Noise filters: common non-actionable warnings to skip
_NOISE_PATTERNS = [
    "RequestsDependencyWarning",
    "Startup notification",
    "Rate limited:",
    "DeprecationWarning",
    "ResourceWarning",
]

# ── Analysis prompt (adapted from claude-hub, without auto_fix) ──

_ANALYSIS_PROMPT = """\
以下是 agentic-feishu 服务 {date} 的错误日志摘要（已按类型分组）：

```json
{errors}
```

对每个错误组，分析：
1. 可能原因（一句话）
2. 可修复性分类：
   - "confirm": 涉及代码或配置修改，需要用户确认
   - "monitor": 瞬态问题（网络超时、第三方 API 波动），无需代码修复，仅监控
3. 修复方案（一句话描述具体做什么，confirm 必填，monitor 留空）

输出 JSON 数组，每项：
{{"level": "...", "error_type": "简短分类", "message": "原始消息摘要", \
"count": N, "cause": "可能原因", "source": "来源模块", \
"fixability": "confirm|monitor", "fix_plan": "修复方案"}}
只输出 JSON，不要其他文字。"""


class ErrorScanner:
    """Simplified 3-phase error scanner."""

    def __init__(self, log_path: str, provider, dispatcher,
                 notify_chat_id: str = ""):
        """
        Args:
            log_path: Path to the log file to scan.
            provider: LLM provider (BaseProvider) for analysis.
            dispatcher: FeishuDispatcher for notifications.
            notify_chat_id: Feishu chat_id for sending reports.
        """
        self._log_path = log_path
        self._provider = provider
        self._dispatcher = dispatcher
        self._notify_chat_id = notify_chat_id

    async def scan(self, date: Optional[str] = None) -> dict:
        """Run the 3-phase error scan pipeline.

        Args:
            date: Date string (YYYY-MM-DD). Defaults to yesterday.

        Returns:
            Summary dict with date, counts, and analyzed results.
        """
        if date is None:
            date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # Phase 1: Parse & group
        raw_errors = self._parse_log(date)
        if not raw_errors:
            log.info("No errors found for %s", date)
            return {"date": date, "total_raw": 0, "groups": 0,
                    "analyzed": [], "confirm_count": 0, "monitor_count": 0}

        grouped = self._group_errors(raw_errors)
        log.info("Found %d raw errors, %d groups for %s",
                 len(raw_errors), len(grouped), date)

        # Phase 2: LLM analysis
        analyzed = await self._analyze(grouped, date)

        # Phase 3: Notify
        await self._notify(analyzed, date)

        confirm_count = sum(1 for a in analyzed
                           if a.get("fixability") == "confirm")
        monitor_count = sum(1 for a in analyzed
                           if a.get("fixability") == "monitor")

        log.info("Error scan complete for %s: %d analyzed, "
                 "%d confirm, %d monitor",
                 date, len(analyzed), confirm_count, monitor_count)

        return {
            "date": date,
            "total_raw": len(raw_errors),
            "groups": len(grouped),
            "analyzed": analyzed,
            "confirm_count": confirm_count,
            "monitor_count": monitor_count,
        }

    # ── Phase 1: Parse log file, group errors ──

    def _parse_log(self, date: str) -> list[dict]:
        """Parse log file, extract ERROR/WARNING lines for the given date."""
        if not os.path.exists(self._log_path):
            log.warning("Log file not found: %s", self._log_path)
            return []

        errors = []
        try:
            with open(self._log_path, "r", encoding="utf-8",
                      errors="replace") as f:
                for line in f:
                    m = _LOG_PATTERN.match(line.strip())
                    if not m:
                        continue
                    ts, source, level, message = m.groups()
                    if not ts.startswith(date):
                        continue
                    if any(n in message for n in _NOISE_PATTERNS):
                        continue
                    errors.append({
                        "timestamp": ts,
                        "source": source,
                        "level": level,
                        "message": message[:500],
                    })
        except Exception as e:
            log.error("Failed to parse log: %s", e)
        return errors

    @staticmethod
    def _group_errors(errors: list[dict]) -> list[dict]:
        """Group similar errors by (level, source, message[:100]), top 30."""
        groups: dict[tuple, dict] = {}
        for e in errors:
            key = (e["level"], e["source"], e["message"][:100])
            if key not in groups:
                groups[key] = {
                    "level": e["level"],
                    "source": e["source"],
                    "message": e["message"],
                    "count": 0,
                }
            groups[key]["count"] += 1
        return sorted(groups.values(),
                      key=lambda x: x["count"], reverse=True)[:30]

    # ── Phase 2: LLM classification ──

    async def _analyze(self, groups: list[dict],
                       date: str) -> list[dict]:
        """LLM classification of error groups."""
        from core.types import Message

        error_summary = json.dumps(groups, ensure_ascii=False, indent=2)
        prompt = _ANALYSIS_PROMPT.format(date=date, errors=error_summary)

        try:
            response = await self._provider.chat(
                messages=[Message(role="user", content=prompt)]
            )
            text = response.text if hasattr(response, "text") else str(response.content)
            parsed = self._parse_json_response(text)
            if isinstance(parsed, list):
                return parsed
        except Exception as e:
            log.warning("LLM analysis failed: %s", e)

        # Fallback: return groups as-is with monitor classification
        return [
            {
                "level": g["level"],
                "error_type": g["source"],
                "message": g["message"][:500],
                "count": g["count"],
                "cause": "(分析失败)",
                "source": g["source"],
                "fixability": "monitor",
                "fix_plan": "",
            }
            for g in groups
        ]

    @staticmethod
    def _parse_json_response(text: str) -> list | dict | None:
        """Parse JSON from LLM response, stripping markdown fences."""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
        try:
            obj, _ = json.JSONDecoder().raw_decode(text.strip())
            return obj
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("Failed to parse JSON response: %s", e)
            return None

    # ── Phase 3: Feishu notification ──

    async def _notify(self, results: list[dict], date: str) -> None:
        """Send Feishu card notification for scan results."""
        if not self._notify_chat_id:
            log.warning("No notify_chat_id configured, skipping notification")
            return

        confirm_items = [r for r in results
                         if r.get("fixability") == "confirm"]
        monitor_items = [r for r in results
                         if r.get("fixability") == "monitor"]

        if not confirm_items and not monitor_items:
            return

        # Only notify if there are confirm items; monitor-only = silent
        if not confirm_items:
            log.info("All %d errors are transient (monitor only), "
                     "no notification sent", len(monitor_items))
            return

        scan_color = "orange" if confirm_items else "grey"
        parts = [
            f"{{{{card:header=错误扫描报告,color={scan_color}}}}}",
            f"**{date}**",
        ]

        # Confirm items (action required)
        if confirm_items:
            parts.append(f"\n**需要确认** ({len(confirm_items)} 项)")
            for item in confirm_items:
                parts.append(
                    f"- **{item.get('error_type', '?')}** "
                    f"({item.get('count', 0)}次): "
                    f"{item.get('cause', '')}\n"
                    f"  方案: {item.get('fix_plan', '无')}"
                )

        # Monitor items (brief mention)
        if monitor_items:
            parts.append(
                f"\n*{len(monitor_items)} 项瞬态错误仅记录监控*")

        try:
            await self._dispatcher.send_card(
                self._notify_chat_id, "\n".join(parts))
        except Exception as e:
            log.error("Failed to send error scan notification: %s", e)
