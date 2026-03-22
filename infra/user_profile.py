# -*- coding: utf-8 -*-
"""Per-user profile management with auto-reflection write-back.

Stores user.md per user — combines OpenClaw's structured preferences
with Hub CC's cognitive profiling. Auto-updated via reflection prompts
at end of conversations.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("agentic.infra.user_profile")

# Default template for new users
DEFAULT_PROFILE = """\
# User Profile

## 基本信息
- 角色：未知
- 时区：未知

## 偏好
- 语言：中文
- 沟通风格：未知

## 认知画像
- 专业背景：未知
- 思维模式：未知
- 决策倾向：未知

## 纠正历史
（无记录）
"""

# Prompt for LLM to extract user profile updates
USER_REFLECTION_PROMPT = """\
对话结束。请回顾这次对话，提取关于用户的新认知。

## 输出格式（JSON）

```json
{
  "profile_updates": [
    {
      "section": "基本信息|偏好|认知画像|纠正历史",
      "action": "add|update",
      "key": "字段名",
      "value": "新值或追加内容"
    }
  ]
}
```

## 规则

- 只提取明确信号，不猜测
- 纠正历史：记录用户对你行为的纠正（包含：纠正内容 + 原因 + 日期）
- 偏好：记录用户明确表达的偏好
- 不记录临时性、单次性的信息
- 没有新认知时返回空数组：{"profile_updates": []}
"""


class UserProfileStore:
    """Per-user profile with file-based persistence."""

    def __init__(self, base_dir: str = "data/users"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, user_id: str) -> Path:
        d = self.base_dir / user_id
        d.mkdir(parents=True, exist_ok=True)
        new_path = d / "profile.md"
        # Migration: move old flat file to new nested path
        old_path = self.base_dir / f"{user_id}.md"
        if old_path.exists() and not new_path.exists():
            import shutil
            shutil.move(str(old_path), str(new_path))
        return new_path

    def load(self, user_id: str) -> str:
        """Load user profile. Returns default template if not found."""
        path = self._path(user_id)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return DEFAULT_PROFILE

    def save(self, user_id: str, content: str) -> None:
        """Save user profile."""
        path = self._path(user_id)
        path.write_text(content, encoding="utf-8")
        log.info("Saved user profile for %s (%d chars)", user_id, len(content))

    def exists(self, user_id: str) -> bool:
        """Check if user has a profile."""
        return self._path(user_id).exists()

    def build_context(self, user_id: str) -> str:
        """Build user profile context for system prompt injection."""
        profile = self.load(user_id)
        if profile == DEFAULT_PROFILE:
            return ""  # Don't inject default template — no value
        return f"## User Profile\n\n{profile}"

    def apply_updates(self, user_id: str, updates: list[dict]) -> int:
        """Apply profile updates from reflection output.

        Simple append/update strategy — for now, just appends notes to
        the relevant section. Full structured editing can come later.

        Args:
            user_id: User ID
            updates: List of dicts with keys: section, action, key, value

        Returns:
            Number of updates applied
        """
        if not updates:
            return 0

        profile = self.load(user_id)
        applied = 0

        for update in updates:
            section = update.get("section", "")
            key = update.get("key", "")
            value = update.get("value", "")

            if not section or not value:
                continue

            # Find the section header
            header = f"## {section}"
            if header not in profile:
                # Append new section
                profile += f"\n{header}\n- {key}：{value}\n"
                applied += 1
            else:
                # Find existing key line and update, or append
                lines = profile.split("\n")
                updated = False
                for i, line in enumerate(lines):
                    if line.strip().startswith(f"- {key}：") or line.strip().startswith(f"- {key}:"):
                        lines[i] = f"- {key}：{value}"
                        updated = True
                        applied += 1
                        break

                if not updated:
                    # Append after section header
                    for i, line in enumerate(lines):
                        if line.strip() == header:
                            # Find end of section (next ## or end of file)
                            insert_at = i + 1
                            while insert_at < len(lines) and not lines[insert_at].startswith("## "):
                                insert_at += 1
                            lines.insert(insert_at, f"- {key}：{value}")
                            applied += 1
                            break

                profile = "\n".join(lines)

        if applied > 0:
            self.save(user_id, profile)
            log.info("Applied %d profile updates for %s", applied, user_id)

        return applied
