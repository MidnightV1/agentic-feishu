# -*- coding: utf-8 -*-
"""Autonomy system — L0-L3 action levels with logging and notify routing.

Levels:
  L0 Silent  — reversible, low-risk, logged only
  L1 Notify  — has changes but reversible, sends notification
  L2 Confirm — irreversible or directional, requires user approval
  L3 Discuss — requires user judgment, blocks on discussion
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Coroutine

log = logging.getLogger("agentic.infra.autonomy")


class ActionLevel(IntEnum):
    L0_SILENT = 0
    L1_NOTIFY = 1
    L2_CONFIRM = 2
    L3_DISCUSS = 3


@dataclass
class AutonomousAction:
    """Record of an autonomous action."""
    level: ActionLevel
    category: str          # e.g., "bug_fix", "config_update", "skill_enhance"
    description: str
    timestamp: float = field(default_factory=time.time)
    result: str = ""       # "success" | "failed" | "pending" | "reverted"
    commit_hash: str = ""  # for L1 actions that create commits
    metadata: dict = field(default_factory=dict)

    @property
    def level_tag(self) -> str:
        return f"L{self.level}"


class AutonomyManager:
    """Manages autonomous action levels, logging, and notification routing."""

    def __init__(
        self,
        log_path: str | Path = "data/autonomy.jsonl",
        notify_callback: Callable[..., Coroutine] | None = None,
    ):
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._notify = notify_callback
        self._actions: list[AutonomousAction] = []
        self._load()

    def _load(self) -> None:
        """Load action history from JSONL."""
        if not self._log_path.exists():
            return
        try:
            for line in self._log_path.read_text().splitlines():
                if line.strip():
                    d = json.loads(line)
                    d["level"] = ActionLevel(d["level"])
                    self._actions.append(AutonomousAction(**d))
        except Exception as e:
            log.warning("Failed to load autonomy log: %s", e)

    def _persist(self, action: AutonomousAction) -> None:
        """Append action to JSONL log."""
        try:
            with open(self._log_path, "a") as f:
                d = asdict(action)
                d["level"] = int(action.level)
                f.write(json.dumps(d) + "\n")
        except Exception as e:
            log.error("Failed to write autonomy log: %s", e)

    async def execute(
        self,
        level: ActionLevel,
        category: str,
        description: str,
        action_fn: Callable[..., Coroutine] | None = None,
        **metadata,
    ) -> AutonomousAction:
        """Execute an autonomous action at the given level.

        L0: Execute silently, log only.
        L1: Execute, log, and notify.
        L2: Log as pending, notify and wait for approval (not auto-executed).
        L3: Log as pending, block on discussion.
        """
        action = AutonomousAction(
            level=level,
            category=category,
            description=description,
            metadata=metadata,
        )

        if level <= ActionLevel.L1_NOTIFY:
            # Execute immediately
            if action_fn:
                try:
                    result = await action_fn()
                    action.result = "success"
                    if isinstance(result, str):
                        action.commit_hash = result
                except Exception as e:
                    action.result = "failed"
                    action.metadata["error"] = str(e)
                    log.error("L%d action failed: %s — %s", level, description, e)
            else:
                action.result = "success"

            # Notify for L1
            if level == ActionLevel.L1_NOTIFY and self._notify:
                try:
                    await self._notify(
                        f"[{action.level_tag}-auto] {description}",
                        action.result,
                    )
                except Exception:
                    log.warning("L1 notification failed for: %s", description)
        else:
            # L2/L3: mark as pending, don't execute
            action.result = "pending"
            if self._notify:
                try:
                    tag = "需确认" if level == ActionLevel.L2_CONFIRM else "需讨论"
                    await self._notify(
                        f"[{action.level_tag}] {tag}: {description}",
                        "pending",
                    )
                except Exception:
                    pass

        self._actions.append(action)
        self._persist(action)
        log.info("Action [%s] %s: %s — %s",
                 action.level_tag, category, description, action.result)
        return action

    # ── Queries ────────────────────────────────────────────────

    def get_recent_actions(self, hours: int = 24) -> list[AutonomousAction]:
        """Get actions from the last N hours."""
        cutoff = time.time() - hours * 3600
        return [a for a in self._actions if a.timestamp >= cutoff]

    def get_pending(self) -> list[AutonomousAction]:
        """Get all pending (unapproved) actions."""
        return [a for a in self._actions if a.result == "pending"]

    def approve(self, index: int) -> bool:
        """Approve a pending action by index in pending list."""
        pending = self.get_pending()
        if 0 <= index < len(pending):
            pending[index].result = "approved"
            return True
        return False
