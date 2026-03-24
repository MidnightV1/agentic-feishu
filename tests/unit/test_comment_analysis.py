# -*- coding: utf-8 -*-
"""Unit tests for document comment analysis: _anchor_quote, _extract_reply_text, analyze_comments."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platforms.feishu.api import _anchor_quote, _extract_reply_text, FeishuAPI


# ── _anchor_quote ──


def test_anchor_exact_match():
    content = "This is the document. Here is the quoted text. And more content."
    result = _anchor_quote(content, "quoted text", ctx_chars=20)
    assert result["matched"] is True
    assert result["quoted"] == "quoted text"
    assert "Here is the" in result["before"]
    assert "And more" in result["after"]


def test_anchor_empty_quote():
    result = _anchor_quote("some content", "", ctx_chars=50)
    assert result["matched"] is False


def test_anchor_not_found():
    result = _anchor_quote("some content", "nonexistent text", ctx_chars=50)
    assert result["matched"] is False
    assert result["quoted"] == "nonexistent text"


def test_anchor_whitespace_normalization():
    content = "This  is   the\n\ndocument   text."
    result = _anchor_quote(content, "is the document", ctx_chars=50)
    assert result["matched"] is True


def test_anchor_partial_fallback():
    """If quote was edited, matches first 40 chars."""
    content = "A" * 30 + "This is a fairly long quote that was partially edited in the document." + "B" * 30
    quote = "This is a fairly long quote that was partially edited in the document and then extended"
    result = _anchor_quote(content, quote, ctx_chars=10)
    assert result["matched"] is True


def test_anchor_ellipsis():
    """Ellipsis added when context is truncated."""
    content = "X" * 300 + " QUOTE " + "Y" * 300
    result = _anchor_quote(content, "QUOTE", ctx_chars=20)
    assert result["before"].startswith("...")
    assert result["after"].endswith("...")


# ── _extract_reply_text ──


def test_extract_text_run():
    reply = {"content": {"elements": [
        {"type": "text_run", "text_run": {"text": "Hello world"}}
    ]}}
    assert _extract_reply_text(reply) == "Hello world"


def test_extract_person_mention():
    reply = {"content": {"elements": [
        {"type": "text_run", "text_run": {"text": "CC "}},
        {"type": "person", "person": {"user_id": "ou_abc"}},
    ]}}
    assert _extract_reply_text(reply) == "CC @ou_abc"


def test_extract_docs_link():
    reply = {"content": {"elements": [
        {"type": "docs_link", "docs_link": {"url": "https://example.com/doc"}},
    ]}}
    assert _extract_reply_text(reply) == "https://example.com/doc"


def test_extract_empty():
    reply = {"content": {"elements": []}}
    assert _extract_reply_text(reply) == "(empty)"


# ── analyze_comments ──


@pytest.fixture
def api():
    a = FeishuAPI.__new__(FeishuAPI)
    a.app_id = "test"
    a.app_secret = "test"
    a.domain = "https://open.feishu.cn"
    a._raw_request = AsyncMock()
    return a


@pytest.mark.asyncio
async def test_analyze_empty_comments(api):
    """No comments returns empty annotations."""
    api.get_document_content = AsyncMock(return_value="Document content here.")
    api._raw_request.return_value = {
        "code": 0,
        "data": {"document": {"title": "Test Doc"}},
    }
    api.list_comments = AsyncMock(return_value=[])

    result = await api.analyze_comments("doc123")
    assert result["annotations"] == []
    assert result["stats"]["shown"] == 0


@pytest.mark.asyncio
async def test_analyze_with_anchoring(api):
    """Comments are anchored to document content."""
    api.get_document_content = AsyncMock(return_value="Introduction. The key finding is X. Conclusion.")
    api._raw_request.return_value = {
        "code": 0,
        "data": {"document": {"title": "Research"}},
    }
    api.list_comments = AsyncMock(return_value=[
        {
            "comment_id": "c1",
            "quote": "key finding",
            "is_resolved": False,
            "replies": [
                {"reply_id": "r1", "content": "Need more detail", "user_id": "ou_abc"},
            ],
        }
    ])

    result = await api.analyze_comments("doc123")
    assert result["title"] == "Research"
    assert len(result["annotations"]) == 1
    ann = result["annotations"][0]
    assert ann["context"]["matched"] is True
    assert ann["thread"][0]["text"] == "Need more detail"


@pytest.mark.asyncio
async def test_analyze_filters_resolved(api):
    """By default, resolved comments are excluded."""
    api.get_document_content = AsyncMock(return_value="Content.")
    api._raw_request.return_value = {
        "code": 0,
        "data": {"document": {"title": "Doc"}},
    }
    api.list_comments = AsyncMock(return_value=[
        {"comment_id": "c1", "quote": "A", "is_resolved": True, "replies": []},
        {"comment_id": "c2", "quote": "B", "is_resolved": False, "replies": []},
    ])

    result = await api.analyze_comments("doc123")
    assert result["stats"]["shown"] == 1
    assert result["annotations"][0]["comment_id"] == "c2"


@pytest.mark.asyncio
async def test_analyze_show_all(api):
    """show_all=True includes resolved comments."""
    api.get_document_content = AsyncMock(return_value="Content.")
    api._raw_request.return_value = {
        "code": 0,
        "data": {"document": {"title": "Doc"}},
    }
    api.list_comments = AsyncMock(return_value=[
        {"comment_id": "c1", "quote": "A", "is_resolved": True, "replies": []},
        {"comment_id": "c2", "quote": "B", "is_resolved": False, "replies": []},
    ])

    result = await api.analyze_comments("doc123", show_all=True)
    assert result["stats"]["shown"] == 2
    assert result["stats"]["filter"] == "all"
