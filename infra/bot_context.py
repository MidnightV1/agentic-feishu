# -*- coding: utf-8 -*-
"""Bot-scoped context — soul, instructions, default persona, shared knowledge.

Each bot has its own context domain under data/bots/{bot_name}/.
Shared knowledge (decisions, facts) is the "glue layer" — accumulated
from all users interacting with this bot.

Files:
    data/bots/{bot_name}/soul.md
    data/bots/{bot_name}/instructions.md
    data/bots/{bot_name}/default_persona.md
    data/bots/{bot_name}/shared/decisions.jsonl
    data/bots/{bot_name}/shared/knowledge.jsonl
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("agentic.infra.bot_context")


@dataclass
class SharedEntry:
    """A single shared knowledge entry."""
    type: str           # "decision" | "fact" | "knowledge"
    title: str
    content: str
    context: str = ""   # how/when this was established
    expires: str = ""   # optional expiry date (ISO)
    created_at: float = 0.0


class BotContext:
    """Loads and serves context for a single bot."""

    def __init__(self, bot_name: str, base_dir: str = "data/bots"):
        self.name = bot_name
        self._dir = Path(base_dir) / bot_name
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "shared").mkdir(exist_ok=True)

    def _read(self, filename: str) -> str:
        path = self._dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return ""

    # ── Static context ──

    @property
    def soul(self) -> str:
        return self._read("soul.md")

    @property
    def instructions(self) -> str:
        return self._read("instructions.md")

    @property
    def default_persona(self) -> str:
        return self._read("default_persona.md")

    # ── Shared knowledge ──

    def _load_jsonl(self, filename: str) -> list[SharedEntry]:
        path = self._dir / "shared" / filename
        if not path.exists():
            return []
        entries: list[SharedEntry] = []
        now_iso = time.strftime("%Y-%m-%d")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                # Skip expired entries
                if d.get("expires") and d["expires"] < now_iso:
                    continue
                entries.append(SharedEntry(**{
                    k: v for k, v in d.items()
                    if k in SharedEntry.__dataclass_fields__
                }))
            except (json.JSONDecodeError, TypeError):
                continue
        return entries

    def _append_jsonl(self, filename: str, entry: SharedEntry) -> None:
        path = self._dir / "shared" / filename
        d = {
            "type": entry.type,
            "title": entry.title,
            "content": entry.content,
            "context": entry.context,
            "expires": entry.expires,
            "created_at": entry.created_at or time.time(),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    def get_shared_knowledge(self) -> list[SharedEntry]:
        """Get all non-expired shared knowledge entries."""
        decisions = self._load_jsonl("decisions.jsonl")
        knowledge = self._load_jsonl("knowledge.jsonl")
        return decisions + knowledge

    def add_shared_entry(self, entry: SharedEntry) -> None:
        """Add a shared knowledge entry (called by crystallization job)."""
        filename = "decisions.jsonl" if entry.type == "decision" else "knowledge.jsonl"
        entry.created_at = entry.created_at or time.time()
        self._append_jsonl(filename, entry)
        log.info("Shared entry added to %s/%s: %s", self.name, filename, entry.title)

    def build_shared_context(self, max_entries: int = 50) -> str:
        """Build shared knowledge text for prompt injection."""
        entries = self.get_shared_knowledge()
        if not entries:
            return ""
        # Most recent first, cap at max_entries
        entries.sort(key=lambda e: e.created_at, reverse=True)
        entries = entries[:max_entries]

        lines = ["## Bot 域共享知识\n"]
        for e in entries:
            label = {"decision": "决策", "fact": "事实", "knowledge": "知识"}.get(e.type, e.type)
            lines.append(f"- **[{label}]** {e.title}: {e.content}")
            if e.context:
                lines.append(f"  背景: {e.context}")
        return "\n".join(lines)
