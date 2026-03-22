# -*- coding: utf-8 -*-
"""SQLite-based session management with async support."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

log = logging.getLogger("agentic.session")

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS sessions (
    session_key TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL DEFAULT 'main',
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    FOREIGN KEY (session_key) REFERENCES sessions(session_key)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_key, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_bot ON sessions(bot_id);
"""


@dataclass
class SessionRecord:
    session_key: str
    bot_id: str = "main"
    provider: str
    model: str
    created_at: float = 0.0
    updated_at: float = 0.0
    message_count: int = 0
    total_cost_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        now = time.time()
        if self.created_at == 0.0:
            self.created_at = now
        if self.updated_at == 0.0:
            self.updated_at = now


class SessionStore:
    """Async SQLite session store for conversation tracking."""

    def __init__(self, db_path: str = "data/sessions.db") -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Open database and create tables if not exist."""
        import os

        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_CREATE_TABLES)
        await self._db.commit()
        log.info("Session store initialized at %s", self.db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SessionStore not initialized — call init() first")
        return self._db

    async def get(self, session_key: str) -> SessionRecord | None:
        """Get a session by key. Returns None if not found."""
        async with self.db.execute(
            "SELECT * FROM sessions WHERE session_key = ?", (session_key,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return SessionRecord(
                session_key=row["session_key"],
                bot_id=row["bot_id"] if "bot_id" in row.keys() else "main",
                provider=row["provider"],
                model=row["model"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                message_count=row["message_count"],
                total_cost_usd=row["total_cost_usd"],
                metadata=json.loads(row["metadata_json"]),
            )

    async def save(self, record: SessionRecord) -> None:
        """Upsert a session record."""
        record.updated_at = time.time()
        await self.db.execute(
            """
            INSERT INTO sessions
                (session_key, bot_id, provider, model, created_at, updated_at,
                 message_count, total_cost_usd, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_key) DO UPDATE SET
                bot_id = excluded.bot_id,
                provider = excluded.provider,
                model = excluded.model,
                updated_at = excluded.updated_at,
                message_count = excluded.message_count,
                total_cost_usd = excluded.total_cost_usd,
                metadata_json = excluded.metadata_json
            """,
            (
                record.session_key,
                record.bot_id,
                record.provider,
                record.model,
                record.created_at,
                record.updated_at,
                record.message_count,
                record.total_cost_usd,
                json.dumps(record.metadata, ensure_ascii=False),
            ),
        )
        await self.db.commit()

    async def list_active(self) -> list[SessionRecord]:
        """List all sessions ordered by most recently updated."""
        async with self.db.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                SessionRecord(
                    session_key=row["session_key"],
                    provider=row["provider"],
                    model=row["model"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    message_count=row["message_count"],
                    total_cost_usd=row["total_cost_usd"],
                    metadata=json.loads(row["metadata_json"]),
                )
                for row in rows
            ]

    async def add_message(
        self,
        session_key: str,
        role: str,
        content: str,
        tool_calls: list[dict] | None = None,
    ) -> int:
        """Append a message to a session. Returns the message id."""
        async with self.db.execute(
            """
            INSERT INTO messages (session_key, role, content, tool_calls_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_key,
                role,
                content,
                json.dumps(tool_calls or [], ensure_ascii=False),
                time.time(),
            ),
        ) as cursor:
            msg_id = cursor.lastrowid

        # Update session message count
        await self.db.execute(
            """
            UPDATE sessions SET message_count = message_count + 1, updated_at = ?
            WHERE session_key = ?
            """,
            (time.time(), session_key),
        )
        await self.db.commit()
        return msg_id  # type: ignore[return-value]

    async def get_messages(
        self, session_key: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get messages for a session, ordered by creation time."""
        async with self.db.execute(
            """
            SELECT role, content, tool_calls_json, created_at
            FROM messages
            WHERE session_key = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (session_key, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "role": row["role"],
                    "content": row["content"],
                    "tool_calls": json.loads(row["tool_calls_json"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    async def get_recent_messages(
        self,
        session_key: str,
        limit: int = 15,
        truncate: int = 4000,
    ) -> list[dict[str, Any]]:
        """Get the most recent messages for a session (newest last).

        Args:
            session_key: Session identifier.
            limit: Maximum number of messages to return (counted as individual
                   messages, not rounds).
            truncate: Max characters per message content. Longer content is
                      cut and suffixed with ``…[truncated]``.

        Returns:
            List of dicts with ``role``, ``content``, ``created_at`` keys,
            ordered oldest-first (chronological).
        """
        # Sub-select newest N rows, then re-order chronologically
        async with self.db.execute(
            """
            SELECT role, content, created_at
            FROM (
                SELECT role, content, created_at
                FROM messages
                WHERE session_key = ?
                ORDER BY created_at DESC
                LIMIT ?
            )
            ORDER BY created_at ASC
            """,
            (session_key, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                content = row["content"]
                if truncate and len(content) > truncate:
                    content = content[: truncate - 13] + "…[truncated]"
                result.append(
                    {
                        "role": row["role"],
                        "content": content,
                        "created_at": row["created_at"],
                    }
                )
            return result

    async def get_recent_messages_by_prefix(
        self,
        session_key_prefix: str,
        limit: int = 15,
        truncate: int = 4000,
    ) -> list[dict[str, Any]]:
        """Get recent messages across all sessions matching a key prefix.

        Useful for chat-level history recovery: after a session is deleted,
        this finds messages from *any* prior session for the same chat.
        The prefix should be e.g. ``"feishu:<chat_id>:"`` to match all
        sessions in that chat regardless of sender.

        Returns chronological order (oldest first).
        """
        async with self.db.execute(
            """
            SELECT role, content, created_at
            FROM (
                SELECT role, content, created_at
                FROM messages
                WHERE session_key LIKE ? || '%'
                ORDER BY created_at DESC
                LIMIT ?
            )
            ORDER BY created_at ASC
            """,
            (session_key_prefix, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                content = row["content"]
                if truncate and len(content) > truncate:
                    content = content[: truncate - 13] + "…[truncated]"
                result.append(
                    {
                        "role": row["role"],
                        "content": content,
                        "created_at": row["created_at"],
                    }
                )
            return result

    async def delete(self, session_key: str) -> None:
        """Delete a session and all its messages."""
        await self.db.execute(
            "DELETE FROM messages WHERE session_key = ?", (session_key,)
        )
        await self.db.execute(
            "DELETE FROM sessions WHERE session_key = ?", (session_key,)
        )
        await self.db.commit()
