# -*- coding: utf-8 -*-
"""Built-in Spreadsheet (Sheet) tools — cell-level read/write.

For Feishu Sheets (电子表格), NOT Bitable (多维表格).
"""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.sheet")

_api: Any = None


def configure(api: Any) -> None:
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for sheet tools")
    return _api


@tool(description="Get spreadsheet metadata and list worksheets", parallel_safe=True)
async def get_spreadsheet_info(spreadsheet_token: str) -> dict:
    """Get metadata and worksheet list for a spreadsheet.

    Args:
        spreadsheet_token: The spreadsheet token (from URL)
    """
    api = _require_api()
    return await api.get_spreadsheet_info(spreadsheet_token)


@tool(description="Read a range of cells from a spreadsheet", parallel_safe=True)
async def read_sheet_range(
    spreadsheet_token: str,
    sheet_id: str,
    range_str: str = "A1:Z100",
) -> list:
    """Read cell values from a spreadsheet range.

    Args:
        spreadsheet_token: The spreadsheet token
        sheet_id: The worksheet ID (from get_spreadsheet_info)
        range_str: Cell range like "A1:D10" (default "A1:Z100")
    """
    api = _require_api()
    return await api.read_sheet_range(spreadsheet_token, sheet_id, range_str)


@tool(description="Write values to a spreadsheet range", parallel_safe=False)
async def write_sheet_range(
    spreadsheet_token: str,
    sheet_id: str,
    range_str: str,
    values: list,
) -> dict:
    """Write values to a spreadsheet range.

    Args:
        spreadsheet_token: The spreadsheet token
        sheet_id: The worksheet ID
        range_str: Cell range like "A1:C3"
        values: 2D list of values (rows × columns)
    """
    api = _require_api()
    return await api.write_sheet_range(spreadsheet_token, sheet_id, range_str, values)
