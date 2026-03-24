# -*- coding: utf-8 -*-
"""Skill-style Drive meta-tool — consolidates all Drive operations into one tool."""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool
from tools.common.url_parser import extract_token
from tools.common.validators import validate_required, validate_action

log = logging.getLogger("agentic.tools.skill_drive")

_api: Any = None

DRIVE_ACTIONS = {"list", "search", "create_folder", "move", "delete"}


def configure(api: Any) -> None:
    """Set the Feishu API client."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for skill_drive")
    return _api


@tool(deferred=True, parallel_safe=False)
async def feishu_drive(action: str, params: dict = {}) -> dict | list:
    """Dispatch Drive operations by action name.

    Args:
        action: One of list, search, create_folder, move, delete
        params: Action-specific parameters (see description)
    """
    api = _require_api()
    validate_action(action, DRIVE_ACTIONS, "feishu_drive")

    # Auto-extract tokens from Feishu URLs
    for key in ("folder_token", "file_token", "parent_token", "target_folder_token"):
        if key in params and params[key]:
            params[key] = extract_token(params[key])

    if action == "list":
        folder_token = params.get("folder_token", "")
        page_size = params.get("page_size", 20)
        return await api.list_drive_files(folder_token, page_size)

    elif action == "search":
        validate_required(params, ["query"])
        query = params.get("query", "")
        count = params.get("count", 10)
        return await api.search_drive(query, count)

    elif action == "create_folder":
        validate_required(params, ["name"])
        name = params.get("name", "")
        parent_token = params.get("parent_token", "")
        if not params.get("force") and parent_token:
            existing_files = await api.list_drive_files(parent_token)
            for f in existing_files:
                if f.get("name") == name and f.get("type") == "folder":
                    return {
                        "status": "duplicate_found",
                        "message": f"已存在同名文件夹：{name}",
                        "existing": f,
                    }
        result = await api.create_drive_folder(name, parent_token)
        from tools.builtin._user_context import get_current_user_id
        from skills.feishu_perm.lib.perm_ops import ensure_user_access

        user_id = get_current_user_id()
        if user_id and result.get("token"):
            perm_result = await ensure_user_access(api, result["token"], "folder", user_id)
            result["auto_collaborator"] = user_id
            if not perm_result.get("success"):
                result["auto_collaborator_error"] = perm_result.get("error", "")
        return result

    elif action == "move":
        validate_required(params, ["file_token", "target_folder_token"])
        file_token = params.get("file_token", "")
        target_folder_token = params.get("target_folder_token", "")
        return await api.move_drive_file(file_token, target_folder_token)

    elif action == "delete":
        if not params.get("confirmed"):
            file_token = params.get("file_token", "unknown")
            return {
                "status": "confirmation_required",
                "message": f"删除云盘文件不可恢复。确认删除文件 {file_token}？请重新调用并设置 confirmed=true",
                "action": action,
                "params": params,
            }
        file_token = params.get("file_token", "")
        file_type = params.get("file_type", "docx")
        return await api.delete_drive_file(file_token, file_type)
