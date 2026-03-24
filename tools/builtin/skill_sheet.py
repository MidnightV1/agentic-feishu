# -*- coding: utf-8 -*-
"""Skill-style Sheet meta-tool — consolidates all Spreadsheet operations into one tool."""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool
from tools.builtin._user_context import get_current_user_id
from tools.common.url_parser import extract_token
from tools.common.validators import validate_required, validate_action

log = logging.getLogger("agentic.tools.skill_sheet")

_api: Any = None

SHEET_ACTIONS = {"create", "info", "read_range", "write_range"}


def configure(api: Any) -> None:
    """Set the Feishu API client."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for skill_sheet")
    return _api


@tool(deferred=True, parallel_safe=False)
async def feishu_sheet(action: str, params: dict = {}) -> dict | list:
    """Dispatch Spreadsheet operations by action name.

    Args:
        action: One of create, info, read_range, write_range
        params: Action-specific parameters (see description)
    """
    api = _require_api()
    validate_action(action, SHEET_ACTIONS, "feishu_sheet")

    # Auto-extract token from Feishu URLs
    if "spreadsheet_token" in params and params["spreadsheet_token"]:
        params["spreadsheet_token"] = extract_token(params["spreadsheet_token"])

    if action == "create":
        validate_required(params, ["title"])
        title = params.get("title", "")
        folder_token = params.get("folder_token", "")
        result = await api.create_spreadsheet(title, folder_token)
        from skills.feishu_perm.lib.perm_ops import ensure_user_access

        user_id = get_current_user_id()
        if user_id and result.get("spreadsheet_token"):
            perm_result = await ensure_user_access(api, result["spreadsheet_token"], "sheet", user_id)
            result["auto_collaborator"] = user_id
            if not perm_result.get("success"):
                result["auto_collaborator_error"] = perm_result.get("error", "unknown")
        return result

    elif action == "info":
        validate_required(params, ["spreadsheet_token"])
        spreadsheet_token = params.get("spreadsheet_token", "")
        return await api.get_spreadsheet_info(spreadsheet_token)

    elif action == "read_range":
        validate_required(params, ["spreadsheet_token", "sheet_id"])
        spreadsheet_token = params.get("spreadsheet_token", "")
        sheet_id = params.get("sheet_id", "")
        range_str = params.get("range_str", "A1:Z100")
        return await api.read_sheet_range(spreadsheet_token, sheet_id, range_str)

    elif action == "write_range":
        validate_required(params, ["spreadsheet_token", "sheet_id", "range_str", "values"])
        spreadsheet_token = params.get("spreadsheet_token", "")
        sheet_id = params.get("sheet_id", "")
        range_str = params.get("range_str", "")
        values = params.get("values", [])
        return await api.write_sheet_range(spreadsheet_token, sheet_id, range_str, values)
