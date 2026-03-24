# -*- coding: utf-8 -*-
"""Unit tests for pagination in query_bitable_records and list_drive_files."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platforms.feishu.api import FeishuAPI


@pytest.fixture
def api():
    a = FeishuAPI.__new__(FeishuAPI)
    a.app_id = "test"
    a.app_secret = "test"
    a.domain = "https://open.feishu.cn"
    a._raw_request = AsyncMock()
    return a


# ── query_bitable_records ──


@pytest.mark.asyncio
async def test_bitable_single_page(api):
    """Single page — no has_more."""
    api._raw_request.return_value = {
        "code": 0,
        "data": {
            "items": [{"record_id": "r1", "fields": {"name": "A"}}],
            "has_more": False,
        },
    }
    result = await api.query_bitable_records("app1", "tbl1")
    assert len(result) == 1
    assert result[0]["record_id"] == "r1"
    api._raw_request.assert_called_once()


@pytest.mark.asyncio
async def test_bitable_multi_page(api):
    """Two pages with page_token."""
    api._raw_request.side_effect = [
        {
            "code": 0,
            "data": {
                "items": [{"record_id": "r1", "fields": {}}],
                "has_more": True,
                "page_token": "pt2",
            },
        },
        {
            "code": 0,
            "data": {
                "items": [{"record_id": "r2", "fields": {}}],
                "has_more": False,
            },
        },
    ]
    result = await api.query_bitable_records("app1", "tbl1")
    assert len(result) == 2
    assert result[0]["record_id"] == "r1"
    assert result[1]["record_id"] == "r2"
    assert api._raw_request.call_count == 2
    # Second call should include page_token
    second_call_body = api._raw_request.call_args_list[1][1].get("body", {})
    assert second_call_body.get("page_token") == "pt2"


@pytest.mark.asyncio
async def test_bitable_max_pages_safety(api):
    """Stops at max_pages even if has_more is True."""
    api._raw_request.return_value = {
        "code": 0,
        "data": {
            "items": [{"record_id": "r1", "fields": {}}],
            "has_more": True,
            "page_token": "next",
        },
    }
    result = await api.query_bitable_records("app1", "tbl1", max_pages=3)
    assert len(result) == 3
    assert api._raw_request.call_count == 3


@pytest.mark.asyncio
async def test_bitable_error_mid_page_returns_partial(api):
    """Error on page 2 returns page 1 results instead of error."""
    api._raw_request.side_effect = [
        {
            "code": 0,
            "data": {
                "items": [{"record_id": "r1", "fields": {}}],
                "has_more": True,
                "page_token": "pt2",
            },
        },
        {"code": 99999, "msg": "internal error"},
    ]
    result = await api.query_bitable_records("app1", "tbl1")
    assert len(result) == 1
    assert result[0]["record_id"] == "r1"


# ── list_drive_files ──


@pytest.mark.asyncio
async def test_drive_single_page(api):
    api._raw_request.return_value = {
        "code": 0,
        "data": {
            "files": [{"token": "t1", "name": "f1", "type": "docx", "url": ""}],
            "has_more": False,
        },
    }
    result = await api.list_drive_files("folder1")
    assert len(result) == 1
    assert result[0]["token"] == "t1"


@pytest.mark.asyncio
async def test_drive_multi_page(api):
    api._raw_request.side_effect = [
        {
            "code": 0,
            "data": {
                "files": [{"token": "t1", "name": "f1", "type": "docx", "url": ""}],
                "has_more": True,
                "page_token": "pt2",
            },
        },
        {
            "code": 0,
            "data": {
                "files": [{"token": "t2", "name": "f2", "type": "sheet", "url": ""}],
                "has_more": False,
            },
        },
    ]
    result = await api.list_drive_files("folder1")
    assert len(result) == 2
    assert result[1]["token"] == "t2"
    # Verify page_token was passed
    second_call_params = api._raw_request.call_args_list[1][1].get("params", {})
    assert second_call_params.get("page_token") == "pt2"


@pytest.mark.asyncio
async def test_drive_max_pages_safety(api):
    api._raw_request.return_value = {
        "code": 0,
        "data": {
            "files": [{"token": "t1", "name": "f1", "type": "docx", "url": ""}],
            "has_more": True,
            "page_token": "next",
        },
    }
    result = await api.list_drive_files("folder1", max_pages=2)
    assert len(result) == 2
    assert api._raw_request.call_count == 2
