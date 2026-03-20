# -*- coding: utf-8 -*-
"""Skill-style Sheet meta-tool — consolidates all Spreadsheet operations into one tool."""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.skill_sheet")

_api: Any = None


def configure(api: Any) -> None:
    """Set the Feishu API client."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for skill_sheet")
    return _api


@tool(
    summary="Feishu Spreadsheet operations: create, info, read_range, write_range",
    deferred=True,
    description="""Feishu Spreadsheet operations.

Actions:
- create: Create spreadsheet. params: {title, folder_token?}. MUST add user as full_access after creation.
- info: Get metadata and worksheets. params: {spreadsheet_token}
- read_range: Read cells. params: {spreadsheet_token, sheet_id, range_str?="A1:Z100"}
- write_range: Write cells. params: {spreadsheet_token, sheet_id, range_str, values} (values = 2D list)
""", parallel_safe=False,
)
async def feishu_sheet(action: str, params: dict = {}) -> dict | list:
    """Dispatch Spreadsheet operations by action name.

    Args:
        action: One of create, info, read_range, write_range
        params: Action-specific parameters (see description)
    """
    api = _require_api()

    if action == "create":
        title = params.get("title", "")
        folder_token = params.get("folder_token", "")
        return await api.create_spreadsheet(title, folder_token)

    elif action == "info":
        spreadsheet_token = params.get("spreadsheet_token", "")
        return await api.get_spreadsheet_info(spreadsheet_token)

    elif action == "read_range":
        spreadsheet_token = params.get("spreadsheet_token", "")
        sheet_id = params.get("sheet_id", "")
        range_str = params.get("range_str", "A1:Z100")
        return await api.read_sheet_range(spreadsheet_token, sheet_id, range_str)

    elif action == "write_range":
        spreadsheet_token = params.get("spreadsheet_token", "")
        sheet_id = params.get("sheet_id", "")
        range_str = params.get("range_str", "")
        values = params.get("values", [])
        return await api.write_sheet_range(spreadsheet_token, sheet_id, range_str, values)

    else:
        return {"error": f"Unknown action: {action}"}
