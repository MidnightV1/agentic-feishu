# -*- coding: utf-8 -*-
"""JSONL backup store — append-only message archive alongside SQLite.

Provides dual-track storage: SQLite for queries, JSONL for debug/audit.
Each session gets its own JSONL file under data/sessions/.

Record schema matches the messages table:
  ts, role, msg_type, content, tool_calls, tool_call_id, tool_name, status
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("agentic.infra.jsonl_store")


class JSONLStore:
    """Append-only JSONL message backup.

    Usage:
        store = JSONLStore("data/sessions")
        store.append(session_key, role="assistant", msg_type="tool_call",
                     content="", tool_calls=[...])
    """

    def __init__(self, base_dir: str | Path = "data/sessions"):
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_key: str) -> Path:
        safe = session_key.replace(":", "_").replace("/", "_")
        return self._base / f"{safe}.jsonl"

    def append(
        self,
        session_key: str,
        role: str,
        content: str,
        *,
        msg_type: str = "user",
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        status: str = "done",
    ) -> None:
        """Append a message to the session's JSONL file."""
        path = self._session_path(session_key)
        record: dict[str, Any] = {
            "ts": time.time(),
            "role": role,
            "msg_type": msg_type,
            "content": content[:50_000],  # safety cap
            "status": status,
        }
        if tool_calls:
            record["tool_calls"] = tool_calls
        if tool_call_id:
            record["tool_call_id"] = tool_call_id
        if tool_name:
            record["tool_name"] = tool_name
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning("JSONL write failed for %s: %s", session_key, e)

    def read(self, session_key: str, limit: int = 100) -> list[dict]:
        """Read recent messages from a session's JSONL file."""
        path = self._session_path(session_key)
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            records = [json.loads(line) for line in lines[-limit:] if line.strip()]
            return records
        except Exception as e:
            log.warning("JSONL read failed for %s: %s", session_key, e)
            return []
