# -*- coding: utf-8 -*-
"""Persistent memory system — file-based, per-user, multi-file with index.

Stores cross-session knowledge organized by type:
  - user: role, preferences, knowledge
  - feedback: corrections, behavioral guidance
  - project: ongoing work, decisions, deadlines
  - reference: external resource pointers

Each memory is a .md file with YAML frontmatter. MEMORY.md serves as index.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger("agentic.infra.memory")

MEMORY_TYPES = {"user", "feedback", "project", "reference"}

# Prompt for LLM to extract memories from a conversation
REFLECTION_PROMPT = """\
对话结束。请回顾这次对话，提取需要持久化到跨会话记忆的信息。

## 记忆类型

- **user**: 用户角色、偏好、专业背景、知识水平
- **feedback**: 用户纠正或行为指导（包含规则 + Why + How to apply）
- **project**: 进行中的工作、决策、截止日期（相对日期转为绝对日期）
- **reference**: 外部资源指针（URL、系统名、用途）

## 输出格式（JSON）

```json
{
  "memory_updates": [
    {
      "type": "user|feedback|project|reference",
      "filename": "descriptive_name.md",
      "action": "create|update|delete",
      "description": "一行描述（用于索引相关性判断）",
      "content": "记忆正文（feedback/project 类型用：规则/事实 + **Why:** + **How to apply:**）"
    }
  ]
}
```

## 规则

- 只记录跨会话有价值的信息
- 不记录代码模式、文件路径、架构细节等可从项目派生的信息
- 不记录 git 历史、调试方案等可从代码/日志派生的信息
- 不重复记录已有信息——优先 update 而非 create
- 没有值得记录的信息时返回空数组：{"memory_updates": []}
"""


class MemoryStore:
    """File-based persistent memory, one directory per user."""

    def __init__(self, base_dir: str = "data/memory"):
        self.base_dir = Path(base_dir)

    def _user_dir(self, user_id: str) -> Path:
        d = self.base_dir / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def load_index(self, user_id: str) -> str:
        """Load MEMORY.md index file for a user."""
        index_path = self._user_dir(user_id) / "MEMORY.md"
        if index_path.exists():
            return index_path.read_text(encoding="utf-8")
        return ""

    def load_file(self, user_id: str, filename: str) -> str:
        """Load a specific memory file."""
        path = self._user_dir(user_id) / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def list_files(self, user_id: str) -> list[str]:
        """List all memory files for a user (excluding MEMORY.md)."""
        user_dir = self._user_dir(user_id)
        return [
            f.name for f in sorted(user_dir.glob("*.md"))
            if f.name != "MEMORY.md"
        ]

    def save(self, user_id: str, filename: str, content: str) -> None:
        """Save a memory file and update the index."""
        path = self._user_dir(user_id) / filename
        path.write_text(content, encoding="utf-8")
        log.info("Saved memory %s/%s (%d chars)", user_id, filename, len(content))
        self._rebuild_index(user_id)

    def delete(self, user_id: str, filename: str) -> bool:
        """Delete a memory file and rebuild index."""
        path = self._user_dir(user_id) / filename
        if path.exists():
            path.unlink()
            log.info("Deleted memory %s/%s", user_id, filename)
            self._rebuild_index(user_id)
            return True
        return False

    def save_index(self, user_id: str, content: str) -> None:
        """Save the MEMORY.md index file."""
        path = self._user_dir(user_id) / "MEMORY.md"
        path.write_text(content, encoding="utf-8")

    def build_context(self, user_id: str, max_chars: int = 8000) -> str:
        """Build memory context string for system prompt injection.

        Loads index + all referenced memory files, truncated to max_chars.
        """
        index = self.load_index(user_id)
        if not index:
            return ""

        parts = [f"## Memory\n\n{index}"]
        total = len(parts[0])

        # Load all memory files referenced in the index
        for filename in self.list_files(user_id):
            content = self.load_file(user_id, filename)
            if not content:
                continue
            entry = f"\n### {filename}\n{content}"
            if total + len(entry) > max_chars:
                parts.append(f"\n(memory truncated at {max_chars} chars)")
                break
            parts.append(entry)
            total += len(entry)

        return "\n".join(parts)

    def apply_updates(self, user_id: str, updates: list[dict]) -> int:
        """Apply memory updates from reflection output.

        Args:
            user_id: User ID
            updates: List of dicts with keys: type, filename, action, description, content

        Returns:
            Number of updates applied
        """
        applied = 0
        index_lines = []

        for update in updates:
            mem_type = update.get("type", "")
            filename = update.get("filename", "")
            action = update.get("action", "create")
            description = update.get("description", "")
            content = update.get("content", "")

            if mem_type not in MEMORY_TYPES:
                log.warning("Unknown memory type: %s", mem_type)
                continue
            if not filename:
                continue

            # Ensure .md extension
            if not filename.endswith(".md"):
                filename += ".md"

            if action == "delete":
                self.delete(user_id, filename)
                applied += 1
            elif action in ("create", "update"):
                # Build file with frontmatter
                file_content = (
                    f"---\n"
                    f"type: {mem_type}\n"
                    f"description: {description}\n"
                    f"---\n\n"
                    f"{content}"
                )
                self.save(user_id, filename, file_content)
                index_lines.append(f"- [{filename}]({filename}) — {description}")
                applied += 1

        # Rebuild index if we made changes
        if applied > 0:
            self._rebuild_index(user_id)

        return applied

    def _rebuild_index(self, user_id: str) -> None:
        """Rebuild MEMORY.md from existing memory files."""
        files = self.list_files(user_id)
        if not files:
            self.save_index(user_id, "# Memory\n\n(empty)\n")
            return

        # Group by type
        grouped: dict[str, list[str]] = {}
        for filename in files:
            content = self.load_file(user_id, filename)
            # Extract type from frontmatter
            type_match = re.search(r"^type:\s*(\w+)", content, re.MULTILINE)
            desc_match = re.search(r"^description:\s*(.+)", content, re.MULTILINE)
            mem_type = type_match.group(1) if type_match else "other"
            description = desc_match.group(1) if desc_match else filename
            grouped.setdefault(mem_type, []).append(f"- [{filename}]({filename}) — {description}")

        lines = ["# Memory\n"]
        for mem_type in ["user", "feedback", "project", "reference", "other"]:
            entries = grouped.get(mem_type, [])
            if entries:
                lines.append(f"## {mem_type.title()}\n")
                lines.extend(entries)
                lines.append("")

        self.save_index(user_id, "\n".join(lines))
