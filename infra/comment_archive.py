# -*- coding: utf-8 -*-
"""Comment archive — persistent storage for Feishu document comments.

Comments disappear when quoted text is edited. This module captures
them before they're lost, storing structured snapshots in SQLite.

Integrates with FeishuAPI.analyze_comments() for analysis data.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("agentic.comment_archive")


class CommentArchive:
    """SQLite-backed comment archive for cross-session tracking."""

    def __init__(self, db_path: str = "data/comment_archive.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS comments (
                doc_id       TEXT NOT NULL,
                comment_id   TEXT NOT NULL,
                quote        TEXT,
                context_before TEXT,
                context_after  TEXT,
                thread       TEXT,
                resolved     INTEGER DEFAULT 0,
                first_seen   REAL NOT NULL,
                last_seen    REAL NOT NULL,
                PRIMARY KEY (doc_id, comment_id)
            );
            CREATE INDEX IF NOT EXISTS idx_doc_id
                ON comments(doc_id);
            CREATE INDEX IF NOT EXISTS idx_last_seen
                ON comments(last_seen);
        """)
        self._conn.commit()

    def archive_comments(self, doc_id: str, annotations: list[dict]) -> int:
        """Store/update comments from an analyze_comments result.

        Args:
            doc_id: Document ID.
            annotations: List of annotation dicts (from analyze_comments).

        Returns:
            Number of new or updated comments.
        """
        now = time.time()
        count = 0

        for ann in annotations:
            comment_id = ann.get("comment_id", "")
            if not comment_id:
                continue

            quote = ann.get("quote", "")
            ctx = ann.get("context", {})
            thread_json = json.dumps(ann.get("thread", []), ensure_ascii=False)
            resolved = 1 if ann.get("resolved", False) else 0

            existing = self._conn.execute(
                "SELECT first_seen FROM comments WHERE doc_id=? AND comment_id=?",
                (doc_id, comment_id),
            ).fetchone()

            if existing:
                self._conn.execute(
                    "UPDATE comments SET quote=?, context_before=?, context_after=?, "
                    "thread=?, resolved=?, last_seen=? "
                    "WHERE doc_id=? AND comment_id=?",
                    (quote, ctx.get("before", ""), ctx.get("after", ""),
                     thread_json, resolved, now,
                     doc_id, comment_id),
                )
            else:
                self._conn.execute(
                    "INSERT INTO comments "
                    "(doc_id, comment_id, quote, context_before, context_after, "
                    " thread, resolved, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (doc_id, comment_id, quote,
                     ctx.get("before", ""), ctx.get("after", ""),
                     thread_json, resolved, now, now),
                )
            count += 1

        self._conn.commit()
        return count

    def query_doc(self, doc_id: str) -> list[dict]:
        """Get all archived comments for a document."""
        rows = self._conn.execute(
            "SELECT comment_id, quote, context_before, context_after, "
            "       thread, resolved, first_seen, last_seen "
            "FROM comments WHERE doc_id=? ORDER BY first_seen",
            (doc_id,),
        ).fetchall()
        return [self._row_to_dict(doc_id, r) for r in rows]

    def query_recent(self, hours: float = 24) -> list[dict]:
        """Get comments seen in the last N hours, across all docs."""
        cutoff = time.time() - hours * 3600
        rows = self._conn.execute(
            "SELECT doc_id, comment_id, quote, context_before, context_after, "
            "       thread, resolved, first_seen, last_seen "
            "FROM comments WHERE last_seen >= ? ORDER BY last_seen DESC",
            (cutoff,),
        ).fetchall()
        return [self._row_to_dict(r[0], r[1:]) for r in rows]

    def digest(self, hours: float = 24) -> dict:
        """Generate a structured digest for daily review."""
        comments = self.query_recent(hours)
        by_doc: dict[str, list] = {}
        for c in comments:
            by_doc.setdefault(c["doc_id"], []).append(c)

        return {
            "period_hours": hours,
            "total_comments": len(comments),
            "docs_with_comments": len(by_doc),
            "by_doc": by_doc,
        }

    def stats(self) -> dict:
        """Archive statistics."""
        total = self._conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        docs = self._conn.execute(
            "SELECT COUNT(DISTINCT doc_id) FROM comments"
        ).fetchone()[0]
        unresolved = self._conn.execute(
            "SELECT COUNT(*) FROM comments WHERE resolved=0"
        ).fetchone()[0]
        return {"total": total, "docs": docs, "unresolved": unresolved}

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_dict(doc_id: str, row: tuple) -> dict:
        comment_id, quote, ctx_before, ctx_after, thread_json, resolved, first_seen, last_seen = row
        try:
            thread = json.loads(thread_json) if thread_json else []
        except json.JSONDecodeError:
            thread = []

        return {
            "doc_id": doc_id,
            "comment_id": comment_id,
            "quote": quote,
            "context_before": ctx_before,
            "context_after": ctx_after,
            "thread": thread,
            "resolved": bool(resolved),
            "first_seen": first_seen,
            "last_seen": last_seen,
        }
