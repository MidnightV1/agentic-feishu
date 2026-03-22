# -*- coding: utf-8 -*-
"""User×Bot relationship — persona overlay, corrections, memory.

Each user×bot pair has its own directory:
    data/users/{user_id}/bots/{bot_name}/
        persona.md           — interaction style (copy-on-write from default)
        persona.md.bak       — backup before persona switch
        corrections.jsonl    — correction history (append-only)
        memory/              — per-relationship memories (md files + index)

Persona lifecycle:
    1. First interaction → copy default_persona.md from bot
    2. Natural adjustment → reflection updates persona.md
    3. Explicit switch → #persona <name> → backup + copy template
    4. Daily crystallization → stable corrections merge into persona
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger("agentic.infra.user_bot_relation")


class UserBotRelation:
    """Manages per-user-per-bot relationship context."""

    def __init__(self, base_dir: str = "data/users"):
        self._base = Path(base_dir)

    def _rel_dir(self, user_id: str, bot_name: str) -> Path:
        d = self._base / user_id / "bots" / bot_name
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Persona ──────────────────────────────────────────────────

    def get_persona(self, user_id: str, bot_name: str) -> str:
        """Get persona text. Returns empty string if not yet initialized."""
        path = self._rel_dir(user_id, bot_name) / "persona.md"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return ""

    def init_persona(self, user_id: str, bot_name: str, default_text: str) -> str:
        """Initialize persona from bot default (copy-on-write).

        Only copies if persona.md doesn't exist yet. Returns the persona text.
        """
        path = self._rel_dir(user_id, bot_name) / "persona.md"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        path.write_text(default_text, encoding="utf-8")
        log.info("Initialized persona for user=%s bot=%s (%d chars)",
                 user_id[:8], bot_name, len(default_text))
        return default_text

    def update_persona(self, user_id: str, bot_name: str, content: str) -> None:
        """Update persona text (from reflection or #persona command)."""
        path = self._rel_dir(user_id, bot_name) / "persona.md"
        path.write_text(content, encoding="utf-8")

    def switch_persona(self, user_id: str, bot_name: str, new_text: str) -> None:
        """Switch persona with backup of current version."""
        d = self._rel_dir(user_id, bot_name)
        current = d / "persona.md"
        backup = d / "persona.md.bak"
        if current.exists():
            shutil.copy2(current, backup)
        current.write_text(new_text, encoding="utf-8")
        log.info("Persona switched for user=%s bot=%s (backup saved)",
                 user_id[:8], bot_name)

    # ── Corrections ──────────────────────────────────────────────

    def add_correction(
        self, user_id: str, bot_name: str,
        rule: str, why: str = "", how_to_apply: str = "",
    ) -> None:
        """Append a correction entry."""
        path = self._rel_dir(user_id, bot_name) / "corrections.jsonl"
        entry = {
            "rule": rule,
            "why": why,
            "how_to_apply": how_to_apply,
            "created_at": time.time(),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_corrections(self, user_id: str, bot_name: str, limit: int = 20) -> list[dict]:
        """Get recent corrections (most recent first)."""
        path = self._rel_dir(user_id, bot_name) / "corrections.jsonl"
        if not path.exists():
            return []
        entries: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries[-limit:]

    def clear_corrections(self, user_id: str, bot_name: str) -> int:
        """Clear corrections after daily crystallization. Returns count cleared."""
        path = self._rel_dir(user_id, bot_name) / "corrections.jsonl"
        if not path.exists():
            return 0
        count = sum(1 for line in path.read_text().splitlines() if line.strip())
        path.write_text("", encoding="utf-8")
        return count

    def build_corrections_context(self, user_id: str, bot_name: str) -> str:
        """Build corrections text for prompt injection."""
        corrections = self.get_corrections(user_id, bot_name)
        if not corrections:
            return ""
        lines = ["## 用户纠正记录（权重最高）\n"]
        for c in corrections:
            lines.append(f"- {c['rule']}")
            if c.get("why"):
                lines.append(f"  Why: {c['why']}")
            if c.get("how_to_apply"):
                lines.append(f"  How: {c['how_to_apply']}")
        return "\n".join(lines)

    # ── Memory ───────────────────────────────────────────────────

    def _memory_dir(self, user_id: str, bot_name: str) -> Path:
        d = self._rel_dir(user_id, bot_name) / "memory"
        d.mkdir(exist_ok=True)
        return d

    def build_memory_context(self, user_id: str, bot_name: str, max_chars: int = 8000) -> str:
        """Build memory text for prompt injection.

        Reads MEMORY.md index + referenced files, capped at max_chars.
        Same file format as MemoryStore but at the user×bot path.
        """
        mem_dir = self._memory_dir(user_id, bot_name)
        index_path = mem_dir / "MEMORY.md"
        if not index_path.exists():
            return ""

        content = index_path.read_text(encoding="utf-8").strip()
        if not content:
            return ""

        # Read referenced memory files
        parts = [content]
        total = len(content)
        for md_file in sorted(mem_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue
            text = md_file.read_text(encoding="utf-8").strip()
            if total + len(text) > max_chars:
                break
            parts.append(f"\n---\n{text}")
            total += len(text)

        return "\n".join(parts)

    # ── Enumeration (for cron jobs) ──────────────────────────────

    def list_relations(self, bot_name: str) -> list[str]:
        """List all user_ids that have a relationship with this bot."""
        user_ids: list[str] = []
        if not self._base.exists():
            return user_ids
        for user_dir in self._base.iterdir():
            if not user_dir.is_dir():
                continue
            bot_dir = user_dir / "bots" / bot_name
            if bot_dir.exists():
                user_ids.append(user_dir.name)
        return user_ids
