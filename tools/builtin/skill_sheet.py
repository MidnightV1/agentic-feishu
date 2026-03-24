# -*- coding: utf-8 -*-
"""Feishu Spreadsheet operations — CLI script for skill invocation.

Called by LLM via bash tool. No @tool registration.

CLI usage:
    python3 tools/builtin/skill_sheet.py <action> --params '<json>'
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

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


# -- CLI entry point ---------------------------------------------------------

async def _cli_main() -> None:
    """CLI entry: python3 skill_sheet.py <action> --params '<json>'"""
    import argparse
    from config.settings import load_settings
    from platforms.feishu.api import FeishuAPI

    parser = argparse.ArgumentParser(description="Feishu Spreadsheet operations")
    parser.add_argument("action", choices=sorted(SHEET_ACTIONS))
    parser.add_argument("--params", default="{}", help="JSON params")
    args = parser.parse_args()

    params = json.loads(args.params)

    settings = load_settings()
    api = FeishuAPI(app_id=settings.feishu.app_id, app_secret=settings.feishu.app_secret)
    await api.start()
    configure(api)

    try:
        result = await feishu_sheet(args.action, params)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        await api.stop()


if __name__ == "__main__":
    asyncio.run(_cli_main())
