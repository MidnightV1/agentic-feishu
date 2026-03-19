# -*- coding: utf-8 -*-
"""Explore loop — processes explore hints from LLM output.

Pipeline:
1. Receive explore hints (from <next-explore> tag)
2. Evaluate relevance and priority
3. Queue for execution
4. Execute (research tasks) and log results
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("agentic.jobs.explorer")


@dataclass
class ExploreItem:
    """A queued exploration item."""
    direction: str
    value: str
    priority: str = "P3"    # P1-P4
    status: str = "queued"  # queued | running | done | skipped
    source: str = "llm"     # llm | user | cron
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0
    result_summary: str = ""


class ExploreQueue:
    """Manages exploration items with persistence.

    Usage:
        queue = ExploreQueue("data/explore.jsonl")
        queue.add("模型能力矩阵设计", "统一降级方案", priority="P2")
        item = queue.next()
        queue.complete(item, "设计完成，见文档 xxx")
    """

    def __init__(self, log_path: str | Path = "data/explore.jsonl"):
        self._path = Path(log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._items: list[ExploreItem] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            for line in self._path.read_text().splitlines():
                if line.strip():
                    self._items.append(ExploreItem(**json.loads(line)))
        except Exception as e:
            log.warning("Failed to load explore queue: %s", e)

    def _save(self) -> None:
        """Rewrite entire file (items are small, this is fine)."""
        try:
            with open(self._path, "w") as f:
                for item in self._items:
                    f.write(json.dumps(asdict(item)) + "\n")
        except Exception as e:
            log.error("Failed to save explore queue: %s", e)

    def add(
        self,
        direction: str,
        value: str,
        priority: str = "P3",
        source: str = "llm",
    ) -> ExploreItem:
        """Add an exploration item to the queue."""
        item = ExploreItem(
            direction=direction,
            value=value,
            priority=priority,
            source=source,
        )
        self._items.append(item)
        self._save()
        log.info("Explore queued [%s]: %s", priority, direction[:60])
        return item

    def add_from_hints(self, hints_text: str) -> int:
        """Parse <next-explore> content and add items.

        Expected format:
        - [方向] description
          [价值] value description
        """
        count = 0
        direction = ""
        for line in hints_text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("- [方向]") or line.startswith("-[方向]"):
                direction = line.split("]", 1)[-1].strip()
            elif line.startswith("[价值]") or line.startswith("  [价值]"):
                value = line.split("]", 1)[-1].strip()
                if direction:
                    self.add(direction, value)
                    count += 1
                    direction = ""
        return count

    def next(self) -> ExploreItem | None:
        """Get the highest-priority queued item."""
        queued = [i for i in self._items if i.status == "queued"]
        if not queued:
            return None
        # Sort by priority (P1 first)
        queued.sort(key=lambda x: x.priority)
        return queued[0]

    def complete(self, item: ExploreItem, summary: str) -> None:
        """Mark an item as completed with a result summary."""
        item.status = "done"
        item.completed_at = time.time()
        item.result_summary = summary
        self._save()

    def skip(self, item: ExploreItem, reason: str = "") -> None:
        item.status = "skipped"
        item.result_summary = reason
        self._save()

    def list_items(self, status: str = "") -> list[ExploreItem]:
        if status:
            return [i for i in self._items if i.status == status]
        return list(self._items)

    def stats(self) -> dict:
        return {
            "total": len(self._items),
            "queued": sum(1 for i in self._items if i.status == "queued"),
            "done": sum(1 for i in self._items if i.status == "done"),
            "skipped": sum(1 for i in self._items if i.status == "skipped"),
        }
