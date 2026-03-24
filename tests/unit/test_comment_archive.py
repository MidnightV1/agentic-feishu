# -*- coding: utf-8 -*-
"""Unit tests for CommentArchive — SQLite persistence for document comments."""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from infra.comment_archive import CommentArchive


@pytest.fixture
def archive(tmp_path):
    """Create a CommentArchive with temporary database."""
    db = str(tmp_path / "test_comments.db")
    a = CommentArchive(db_path=db)
    yield a
    a.close()


def _make_annotations(n: int = 3, resolved: bool = False) -> list[dict]:
    return [
        {
            "comment_id": f"c{i}",
            "quote": f"Quoted text {i}",
            "context": {"before": f"before {i}", "after": f"after {i}", "matched": True},
            "thread": [{"user_id": "ou_abc", "text": f"Comment {i}"}],
            "resolved": resolved,
        }
        for i in range(n)
    ]


class TestArchiveComments:
    def test_insert_new(self, archive):
        anns = _make_annotations(3)
        count = archive.archive_comments("doc1", anns)
        assert count == 3

    def test_upsert_existing(self, archive):
        anns = _make_annotations(2)
        archive.archive_comments("doc1", anns)
        # Archive again with updated thread
        anns[0]["thread"].append({"user_id": "ou_xyz", "text": "Reply"})
        count = archive.archive_comments("doc1", anns)
        assert count == 2
        # Verify thread was updated
        comments = archive.query_doc("doc1")
        c0 = next(c for c in comments if c["comment_id"] == "c0")
        assert len(c0["thread"]) == 2

    def test_skip_empty_comment_id(self, archive):
        anns = [{"comment_id": "", "quote": "x"}]
        count = archive.archive_comments("doc1", anns)
        assert count == 0

    def test_preserves_first_seen(self, archive):
        anns = _make_annotations(1)
        archive.archive_comments("doc1", anns)
        first = archive.query_doc("doc1")[0]["first_seen"]
        time.sleep(0.01)
        archive.archive_comments("doc1", anns)
        updated = archive.query_doc("doc1")[0]
        assert updated["first_seen"] == first  # unchanged
        assert updated["last_seen"] > first    # updated


class TestQueryDoc:
    def test_returns_for_doc(self, archive):
        archive.archive_comments("doc1", _make_annotations(2))
        archive.archive_comments("doc2", _make_annotations(1))
        result = archive.query_doc("doc1")
        assert len(result) == 2
        assert all(c["doc_id"] == "doc1" for c in result)

    def test_empty_doc(self, archive):
        assert archive.query_doc("nonexistent") == []


class TestQueryRecent:
    def test_recent_within_window(self, archive):
        archive.archive_comments("doc1", _make_annotations(2))
        result = archive.query_recent(hours=1)
        assert len(result) == 2

    def test_recent_excludes_old(self, archive):
        archive.archive_comments("doc1", _make_annotations(1))
        # Manually set last_seen to 48 hours ago
        archive._conn.execute(
            "UPDATE comments SET last_seen = ?",
            (time.time() - 48 * 3600,),
        )
        archive._conn.commit()
        result = archive.query_recent(hours=24)
        assert len(result) == 0


class TestDigest:
    def test_digest_structure(self, archive):
        archive.archive_comments("doc1", _make_annotations(2))
        archive.archive_comments("doc2", _make_annotations(1))
        d = archive.digest(hours=1)
        assert d["total_comments"] == 3
        assert d["docs_with_comments"] == 2
        assert "doc1" in d["by_doc"]
        assert len(d["by_doc"]["doc1"]) == 2


class TestStats:
    def test_stats(self, archive):
        archive.archive_comments("doc1", _make_annotations(2, resolved=False))
        archive.archive_comments("doc2", _make_annotations(1, resolved=True))
        s = archive.stats()
        assert s["total"] == 3
        assert s["docs"] == 2
        assert s["unresolved"] == 2


class TestRowToDict:
    def test_deserializes_thread(self, archive):
        anns = _make_annotations(1)
        archive.archive_comments("doc1", anns)
        result = archive.query_doc("doc1")[0]
        assert isinstance(result["thread"], list)
        assert result["thread"][0]["user_id"] == "ou_abc"

    def test_resolved_as_bool(self, archive):
        anns = _make_annotations(1, resolved=True)
        archive.archive_comments("doc1", anns)
        result = archive.query_doc("doc1")[0]
        assert result["resolved"] is True
