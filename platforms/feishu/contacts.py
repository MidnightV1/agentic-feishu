# -*- coding: utf-8 -*-
"""SQLite-based contact store: name -> open_id mapping with auto-learning."""

from __future__ import annotations

import sqlite3
import logging
from pathlib import Path

log = logging.getLogger("agentic.feishu.contacts")


class ContactStore:
    """Persistent name -> open_id mapping."""

    def __init__(self, db_path: str = "data/contacts.db"):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                name TEXT PRIMARY KEY,
                open_id TEXT NOT NULL,
                source TEXT DEFAULT 'manual',
                updated_at REAL DEFAULT (strftime('%s','now'))
            )
        """)
        self._conn.commit()

    def resolve(self, name: str) -> str | None:
        """Resolve a display name to open_id."""
        row = self._conn.execute(
            "SELECT open_id FROM contacts WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row else None

    def learn(self, name: str, open_id: str, source: str = "auto") -> None:
        """Add or update a contact mapping."""
        self._conn.execute(
            "INSERT OR REPLACE INTO contacts (name, open_id, source, updated_at) "
            "VALUES (?, ?, ?, strftime('%s','now'))",
            (name, open_id, source),
        )
        self._conn.commit()
        log.debug("Contact learned: %s -> %s (source=%s)", name, open_id, source)

    def list_all(self) -> list[dict]:
        """List all contacts."""
        rows = self._conn.execute(
            "SELECT name, open_id, source FROM contacts ORDER BY name"
        ).fetchall()
        return [{"name": r[0], "open_id": r[1], "source": r[2]} for r in rows]

    def delete(self, name: str) -> bool:
        """Delete a contact by name."""
        cursor = self._conn.execute("DELETE FROM contacts WHERE name = ?", (name,))
        self._conn.commit()
        return cursor.rowcount > 0
