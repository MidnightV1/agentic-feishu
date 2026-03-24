# -*- coding: utf-8 -*-
"""Unit tests for document write flow: append_document, _create_nested_list, reply_comment.

Mocks _raw_request to test block dispatch logic without hitting Feishu API.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platforms.feishu.api import FeishuAPI


@pytest.fixture
def api():
    """Create a FeishuAPI with mocked _raw_request."""
    a = FeishuAPI.__new__(FeishuAPI)
    a.app_id = "test"
    a.app_secret = "test"
    a.domain = "https://open.feishu.cn"
    a._raw_request = AsyncMock(return_value={"code": 0, "data": {}})
    return a


# ── append_document: basic blocks ──


class TestAppendDocumentBasic:
    @pytest.mark.asyncio
    async def test_empty_content(self, api):
        result = await api.append_document("doc123", "")
        assert result["ok"]
        assert result["blocks_added"] == 0
        api._raw_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_plain_text(self, api):
        result = await api.append_document("doc123", "Hello world")
        assert result["ok"]
        assert result["blocks_added"] >= 1
        # Should POST to children endpoint
        api._raw_request.assert_called()
        call_args = api._raw_request.call_args
        assert "children" in call_args[0][1]
        body = call_args[1].get("body") or call_args[0][2]
        blocks = body["children"]
        assert blocks[0]["block_type"] == 2

    @pytest.mark.asyncio
    async def test_heading_and_list(self, api):
        md = "# 标题\n- 项目A\n- 项目B"
        result = await api.append_document("doc123", md)
        assert result["ok"]
        assert result["blocks_added"] >= 3

    @pytest.mark.asyncio
    async def test_batch_size_chunking(self, api):
        """More than 50 blocks should be sent in multiple batches."""
        lines = [f"Line {i}" for i in range(60)]
        md = "\n".join(lines)
        result = await api.append_document("doc123", md)
        assert result["ok"]
        # Should have made at least 2 POST calls (50 + 10)
        post_calls = [c for c in api._raw_request.call_args_list
                      if c[0][0] == "POST" and "children" in c[0][1]]
        assert len(post_calls) >= 2


# ── append_document: nested lists via descendant API ──


class TestAppendDocumentNestedList:
    @pytest.mark.asyncio
    async def test_nested_list_calls_descendant_api(self, api):
        """Nested list should trigger descendant API, not children API."""
        md = "- 第一层\n  - 第二层\n- 第三项"
        result = await api.append_document("doc123", md)
        assert result["ok"]
        # Find the descendant API call
        descendant_calls = [c for c in api._raw_request.call_args_list
                            if "descendant" in str(c)]
        assert len(descendant_calls) == 1
        body = descendant_calls[0][1].get("body") or descendant_calls[0][0][2]
        assert "children_id" in body
        assert "descendants" in body
        assert len(body["children_id"]) >= 1

    @pytest.mark.asyncio
    async def test_nested_list_descendant_payload_structure(self, api):
        """Verify descendant payload has correct temp IDs and parent-child links."""
        md = "- Parent\n  - Child A\n  - Child B"
        await api.append_document("doc123", md)
        descendant_calls = [c for c in api._raw_request.call_args_list
                            if "descendant" in str(c)]
        body = descendant_calls[0][1].get("body") or descendant_calls[0][0][2]
        descendants = body["descendants"]
        # Should have parent + 2 children = 3 descriptors
        assert len(descendants) >= 3
        # Each descriptor must have block_id starting with tmp_
        for d in descendants:
            assert d["block_id"].startswith("tmp_")
            assert "block_type" in d

    @pytest.mark.asyncio
    async def test_nested_list_fallback_on_api_failure(self, api):
        """If descendant API fails, should degrade to flat blocks."""
        call_count = [0]
        original_mock = api._raw_request

        async def side_effect(method, path, body=None, params=None):
            call_count[0] += 1
            if "descendant" in path:
                return {"code": 99999, "msg": "descendant not supported"}
            return {"code": 0, "data": {}}

        api._raw_request = AsyncMock(side_effect=side_effect)
        md = "- Parent\n  - Child\n- Sibling"
        result = await api.append_document("doc123", md)
        assert result["ok"]
        # Should have called descendant (failed) then children (fallback)
        children_calls = [c for c in api._raw_request.call_args_list
                          if "children" in str(c[0][1]) and "descendant" not in str(c[0][1])]
        assert len(children_calls) >= 1
        # Fallback blocks should include parent + child + sibling (flat)
        body = children_calls[0][1].get("body") or children_calls[0][0][2]
        assert len(body["children"]) >= 3  # all items flattened


# ── append_document: tables ──


class TestAppendDocumentTables:
    @pytest.mark.asyncio
    async def test_table_creates_table_block(self, api):
        """Table markdown should trigger table creation API."""
        # Mock: resp["data"]["children"][0] → table block with cells
        # 2 rows (header + 1 data) × 2 cols = 4 cells
        api._raw_request = AsyncMock(side_effect=[
            # 1. Create table → children[0] has table.cells
            {"code": 0, "data": {"children": [{"block_id": "tbl_123", "table": {"cells": ["c1", "c2", "c3", "c4"]}}]}},
            # 2-5. GET cell children → text block inside each cell
            {"code": 0, "data": {"items": [{"block_id": "txt_1"}]}},
            {"code": 0, "data": {"items": [{"block_id": "txt_2"}]}},
            {"code": 0, "data": {"items": [{"block_id": "txt_3"}]}},
            {"code": 0, "data": {"items": [{"block_id": "txt_4"}]}},
            # 6. batch_update cells
            {"code": 0, "data": {}},
        ])
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = await api.append_document("doc123", md)
        assert result["ok"]
        assert result["blocks_added"] >= 1


# ── append_document: rollback on failure ──


class TestAppendDocumentRollback:
    @pytest.mark.asyncio
    async def test_rollback_deletes_created_blocks_on_exception(self, api):
        """If flush raises, previously created block IDs should be deleted."""
        call_count = [0]

        async def side_effect(method, path, body=None, params=None):
            call_count[0] += 1
            if method == "POST" and "children" in path and "descendant" not in path:
                if call_count[0] == 1:
                    # First flush succeeds, returns block IDs
                    return {"code": 0, "data": {"children": [
                        {"block_id": "blk_a"}, {"block_id": "blk_b"},
                    ]}}
                else:
                    # Second flush raises exception
                    raise RuntimeError("network failure")
            return {"code": 0, "data": {}}

        api._raw_request = AsyncMock(side_effect=side_effect)
        # 60 lines → 2 batches → first succeeds, second fails
        lines = [f"Line {i}" for i in range(60)]
        md = "\n".join(lines)

        with pytest.raises(RuntimeError, match="network failure"):
            await api.append_document("doc123", md)

        # Rollback: should have called DELETE for blk_a and blk_b (reverse order)
        delete_calls = [c for c in api._raw_request.call_args_list
                        if c[0][0] == "DELETE"]
        assert len(delete_calls) == 2
        # Reverse order: blk_b first, blk_a second
        assert "blk_b" in delete_calls[0][0][1]
        assert "blk_a" in delete_calls[1][0][1]

    @pytest.mark.asyncio
    async def test_no_rollback_when_no_blocks_created(self, api):
        """If failure happens before any blocks are created, no DELETE calls."""
        api._raw_request = AsyncMock(side_effect=RuntimeError("immediate failure"))
        md = "Hello world"

        with pytest.raises(RuntimeError, match="immediate failure"):
            await api.append_document("doc123", md)

        # No DELETE calls since nothing was created
        delete_calls = [c for c in api._raw_request.call_args_list
                        if c[0][0] == "DELETE"]
        assert len(delete_calls) == 0


# ── _create_nested_list ──


class TestCreateNestedList:
    @pytest.mark.asyncio
    async def test_basic_structure(self, api):
        """Verify descendant API is called with correct structure."""
        items = [
            {"type": "bullet", "depth": 0, "elements": [{"text_run": {"content": "Parent"}}]},
            {"type": "bullet", "depth": 1, "elements": [{"text_run": {"content": "Child"}}]},
        ]
        count = await api._create_nested_list("doc123", items)
        assert count == 1
        call_args = api._raw_request.call_args
        body = call_args[1].get("body") or call_args[0][2]
        assert len(body["children_id"]) == 1
        assert len(body["descendants"]) == 2  # parent + child
        # Parent should reference child
        parent_desc = next(d for d in body["descendants"] if d["block_id"] == body["children_id"][0])
        assert "children" in parent_desc
        assert len(parent_desc["children"]) == 1

    @pytest.mark.asyncio
    async def test_multiple_parents(self, api):
        items = [
            {"type": "bullet", "depth": 0, "elements": [{"text_run": {"content": "P1"}}]},
            {"type": "bullet", "depth": 1, "elements": [{"text_run": {"content": "C1"}}]},
            {"type": "ordered", "depth": 0, "elements": [{"text_run": {"content": "P2"}}]},
        ]
        count = await api._create_nested_list("doc123", items)
        assert count == 2
        body = api._raw_request.call_args[1].get("body") or api._raw_request.call_args[0][2]
        assert len(body["children_id"]) == 2
        assert len(body["descendants"]) == 3  # P1 + C1 + P2

    @pytest.mark.asyncio
    async def test_3_level_nesting(self, api):
        """Three-level nesting: grandchild correctly attached."""
        items = [
            {"type": "bullet", "depth": 0, "elements": [{"text_run": {"content": "L0"}}]},
            {"type": "bullet", "depth": 1, "elements": [{"text_run": {"content": "L1"}}]},
            {"type": "bullet", "depth": 2, "elements": [{"text_run": {"content": "L2"}}]},
        ]
        count = await api._create_nested_list("doc123", items)
        assert count == 1  # only L0 is top-level
        body = api._raw_request.call_args[1].get("body") or api._raw_request.call_args[0][2]
        assert len(body["children_id"]) == 1
        assert len(body["descendants"]) == 3
        # L0 → L1, L1 → L2
        descs = {d["block_id"]: d for d in body["descendants"]}
        l0 = descs[body["children_id"][0]]
        assert "children" in l0
        l1_id = l0["children"][0]
        l1 = descs[l1_id]
        assert "children" in l1
        assert len(l1["children"]) == 1

    @pytest.mark.asyncio
    async def test_api_error_returns_zero(self, api):
        api._raw_request = AsyncMock(return_value={"code": 400, "msg": "bad request"})
        items = [
            {"type": "bullet", "depth": 0, "elements": []},
            {"type": "bullet", "depth": 1, "elements": []},
        ]
        count = await api._create_nested_list("doc123", items)
        assert count == 0

    @pytest.mark.asyncio
    async def test_empty_items_returns_zero(self, api):
        count = await api._create_nested_list("doc123", [])
        assert count == 0
        api._raw_request.assert_not_called()


# ── reply_comment ──


class TestReplyComment:
    @pytest.mark.asyncio
    async def test_rich_text_body_format(self, api):
        """reply_comment should send rich text structure, not plain string."""
        api._raw_request = AsyncMock(return_value={"code": 0, "data": {"reply": {"id": "r1"}}})
        result = await api.reply_comment("doc123", "comment456", "回复内容")
        assert result["ok"]
        call_args = api._raw_request.call_args
        body = call_args[1].get("body") or call_args[0][2]
        # Must be rich text structure
        assert "content" in body
        assert "elements" in body["content"]
        elem = body["content"]["elements"][0]
        assert elem["type"] == "text_run"
        assert elem["text_run"]["content"] == "回复内容"

    @pytest.mark.asyncio
    async def test_reply_error_returns_error(self, api):
        api._raw_request = AsyncMock(return_value={"code": 400, "msg": "bad"})
        result = await api.reply_comment("doc123", "c1", "text")
        assert "error" in result
