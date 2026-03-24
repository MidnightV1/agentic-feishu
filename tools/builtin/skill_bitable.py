# -*- coding: utf-8 -*-
"""Feishu Bitable operations — CLI script for skill invocation.

Called by LLM via bash tool. No @tool registration.

CLI usage:
    python3 tools/builtin/skill_bitable.py <action> --params '<json>'
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from tools.builtin._user_context import get_current_user_id
from tools.common.url_parser import extract_token, extract_bitable_table_id
from tools.common.validators import validate_required, validate_action

log = logging.getLogger("agentic.tools.skill_bitable")

_api: Any = None

BITABLE_ACTIONS = {"create", "list_tables", "query", "add_record", "update_record", "delete_record"}


def configure(api: Any) -> None:
    """Set the Feishu API client."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for skill_bitable")
    return _api


async def feishu_bitable(action: str, params: dict = {}) -> dict | list:
    """Dispatch Bitable operations by action name.

    Args:
        action: One of create, list_tables, query, add_record, update_record, delete_record
        params: Action-specific parameters (see description)
    """
    api = _require_api()
    validate_action(action, BITABLE_ACTIONS, "feishu_bitable")

    # Auto-extract tokens from Feishu URLs
    if "app_token" in params and params["app_token"]:
        params["app_token"] = extract_token(params["app_token"])
    if "table_id" in params and params["table_id"]:
        params["table_id"] = extract_bitable_table_id(params["table_id"])

    if action == "create":
        validate_required(params, ["name"])
        name = params.get("name", "")
        folder_token = params.get("folder_token", "")
        result = await api.create_bitable(name, folder_token)
        from skills.feishu_perm.lib.perm_ops import ensure_user_access

        user_id = get_current_user_id()
        if user_id and result.get("app_token"):
            perm_result = await ensure_user_access(api, result["app_token"], "bitable", user_id)
            result["auto_collaborator"] = user_id
            if not perm_result.get("success"):
                result["auto_collaborator_error"] = perm_result.get("error", "unknown")
        return result

    elif action == "list_tables":
        validate_required(params, ["app_token"])
        app_token = params.get("app_token", "")
        return await api.list_bitable_tables(app_token)

    elif action == "query":
        validate_required(params, ["app_token", "table_id"])
        app_token = params.get("app_token", "")
        table_id = params.get("table_id", "")
        filter_expr = params.get("filter_expr", "")
        page_size = params.get("page_size", 20)
        return await api.query_bitable_records(
            app_token, table_id, filter_expr=filter_expr, page_size=page_size
        )

    elif action == "add_record":
        validate_required(params, ["app_token", "table_id", "fields"])
        app_token = params.get("app_token", "")
        table_id = params.get("table_id", "")
        fields = params.get("fields", {})
        return await api.add_bitable_record(app_token, table_id, fields)

    elif action == "update_record":
        validate_required(params, ["app_token", "table_id", "record_id", "fields"])
        app_token = params.get("app_token", "")
        table_id = params.get("table_id", "")
        record_id = params.get("record_id", "")
        fields = params.get("fields", {})
        return await api.update_bitable_record(app_token, table_id, record_id, fields)

    elif action == "delete_record":
        validate_required(params, ["app_token", "table_id", "record_id"])
        app_token = params.get("app_token", "")
        table_id = params.get("table_id", "")
        record_id = params.get("record_id", "")
        return await api.delete_bitable_record(app_token, table_id, record_id)


# -- CLI entry point ---------------------------------------------------------

async def _cli_main() -> None:
    """CLI entry: python3 skill_bitable.py <action> --params '<json>'"""
    import argparse
    from config.settings import load_settings
    from platforms.feishu.api import FeishuAPI

    parser = argparse.ArgumentParser(description="Feishu Bitable operations")
    parser.add_argument("action", choices=sorted(BITABLE_ACTIONS))
    parser.add_argument("--params", default="{}", help="JSON params")
    args = parser.parse_args()

    params = json.loads(args.params)

    settings = load_settings()
    api = FeishuAPI(app_id=settings.feishu.app_id, app_secret=settings.feishu.app_secret)
    await api.start()
    configure(api)

    try:
        result = await feishu_bitable(args.action, params)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        await api.stop()


if __name__ == "__main__":
    asyncio.run(_cli_main())
