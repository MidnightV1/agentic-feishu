# -*- coding: utf-8 -*-
"""SQLite-based contact store: open_id <-> name mapping with auto-learning from messages."""

from __future__ import annotations

import asyncio
import sqlite3
import logging
from pathlib import Path

log = logging.getLogger("agentic.feishu.contacts")


class ContactStore:
    """Persistent open_id <-> name mapping with auto-learning.

    Auto-learns sender identity from every incoming message.
    Resolves names to open_ids for tools (calendar attendees, task assignment, etc).
    """

    def __init__(self, db_path: str = "data/contacts.db"):
        self._db_path = db_path
        self._feishu_api = None  # set via set_api()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                open_id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                source TEXT DEFAULT 'auto',
                updated_at REAL DEFAULT (strftime('%s','now'))
            )
        """)
        self._conn.commit()
        log.info("ContactStore initialized (%d contacts)", self.count())

    def set_api(self, feishu_api) -> None:
        """Set Feishu API client for fetching user profiles."""
        self._feishu_api = feishu_api

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM contacts").fetchone()
        return row[0] if row else 0

    def resolve_name(self, name: str) -> str | None:
        """Resolve a display name to open_id (fuzzy: LIKE %name%)."""
        row = self._conn.execute(
            "SELECT open_id FROM contacts WHERE name LIKE ? LIMIT 1",
            (f"%{name}%",),
        ).fetchone()
        return row[0] if row else None

    def resolve_id(self, open_id: str) -> str | None:
        """Resolve an open_id to display name."""
        row = self._conn.execute(
            "SELECT name FROM contacts WHERE open_id = ?", (open_id,)
        ).fetchone()
        return row[0] if row else None

    def learn(self, open_id: str, name: str, source: str = "auto") -> None:
        """Add or update a contact mapping."""
        if not open_id or not name:
            return
        self._conn.execute(
            "INSERT INTO contacts (open_id, name, source, updated_at) "
            "VALUES (?, ?, ?, strftime('%s','now')) "
            "ON CONFLICT(open_id) DO UPDATE SET name=excluded.name, "
            "source=excluded.source, updated_at=excluded.updated_at",
            (open_id, name, source),
        )
        self._conn.commit()
        log.debug("Contact learned: %s -> %s (source=%s)", name, open_id, source)

    async def auto_learn(self, open_id: str) -> str:
        """Auto-learn from Feishu API if not already known. Returns name."""
        existing = self.resolve_id(open_id)
        if existing and not existing.startswith("ou_"):
            return existing

        # Fetch from Feishu API
        if self._feishu_api:
            name = await self._fetch_name(open_id)
            if name:
                self.learn(open_id, name, source="feishu_api")
                return name

        return existing or ""

    async def _fetch_name(self, open_id: str) -> str:
        """Fetch user display name from Feishu contact API."""
        try:
            resp = await asyncio.to_thread(
                self._feishu_api.get,
                f"/open-apis/contact/v3/users/{open_id}",
                params={"user_id_type": "open_id"},
            )
            data = resp.get("data", {}).get("user", {})
            return data.get("name", "")
        except Exception as e:
            log.warning("Failed to fetch user name for %s: %s", open_id[:10], e)
            return ""

    def list_all(self) -> list[dict]:
        """List all contacts."""
        rows = self._conn.execute(
            "SELECT open_id, name, source FROM contacts ORDER BY name"
        ).fetchall()
        return [{"open_id": r[0], "name": r[1], "source": r[2]} for r in rows]

    def delete(self, open_id: str) -> bool:
        """Delete a contact by open_id."""
        cursor = self._conn.execute("DELETE FROM contacts WHERE open_id = ?", (open_id,))
        self._conn.commit()
        return cursor.rowcount > 0
