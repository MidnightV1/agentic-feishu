"""Persistent contact store shared across skills."""

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("agentic.contacts")


class ContactStore:
    """持久化联系人库。自动从 API 响应中学习。"""

    def __init__(self, data_dir: str = "data"):
        self._path = Path(data_dir) / "contacts.json"
        self._contacts: dict[str, dict[str, Any]] = {}  # open_id → {name, aliases, source, ...}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._contacts = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("Failed to load contacts from %s", self._path)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._contacts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def learn(self, open_id: str, name: str, source: str = "manual") -> None:
        """从 API 响应中自动学习联系人。"""
        if open_id in self._contacts:
            existing = self._contacts[open_id]
            if name and name != existing.get("name"):
                existing.setdefault("aliases", [])
                old_name = existing.get("name", "")
                if old_name and old_name not in existing["aliases"]:
                    existing["aliases"].append(old_name)
                existing["name"] = name
        else:
            self._contacts[open_id] = {"name": name, "aliases": [], "source": source}
        self._save()

    def resolve(self, name_or_id: str) -> str | None:
        """名称 → open_id 解析。支持精确匹配和模糊匹配。"""
        # 1. 直接是 open_id
        if name_or_id.startswith("ou_"):
            # Pass through; return as-is even if not yet in store
            return name_or_id

        # 2. 精确匹配 name 或 alias
        for oid, info in self._contacts.items():
            if info.get("name") == name_or_id:
                return oid
            if name_or_id in info.get("aliases", []):
                return oid

        # 3. 模糊匹配（包含关系，大小写不敏感）
        query_lower = name_or_id.lower()
        for oid, info in self._contacts.items():
            if query_lower in info.get("name", "").lower():
                return oid
            if any(query_lower in alias.lower() for alias in info.get("aliases", [])):
                return oid

        return None

    def search(self, query: str) -> list[dict]:
        """搜索联系人。返回 [{open_id, name, aliases}]"""
        results: list[dict] = []
        q = query.lower()
        for oid, info in self._contacts.items():
            name = info.get("name", "")
            aliases = info.get("aliases", [])
            if (
                q in name.lower()
                or q in oid.lower()
                or any(q in a.lower() for a in aliases)
            ):
                results.append({"open_id": oid, "name": name, "aliases": aliases})
        return results

    def list_all(self) -> list[dict]:
        """列出所有已知联系人。"""
        return [{"open_id": oid, **info} for oid, info in self._contacts.items()]
