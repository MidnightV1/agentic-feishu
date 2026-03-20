# -*- coding: utf-8 -*-
"""Skill-style Drive meta-tool — consolidates all Drive operations into one tool."""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.skill_drive")

_api: Any = None


def configure(api: Any) -> None:
    """Set the Feishu API client."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for skill_drive")
    return _api


@tool(
    summary="Feishu Drive operations: list, search, create_folder, move, delete",
    deferred=True,
    description="""Feishu Drive operations.

Actions:
- list: List files/folders. params: {folder_token?, page_size?=20}
- search: Search files. params: {query, count?=10}
- create_folder: Create folder. params: {name, parent_token} (parent_token REQUIRED)
- move: Move file/folder. params: {file_token, target_folder_token}
- delete: Delete file/folder (moves to trash, recoverable 30 days). params: {file_token, file_type?="docx"}. file_type: docx, sheet, bitable, mindnote, file, folder. IRREVERSIBLE after trash period — confirm with user first.
""", parallel_safe=False,
)
async def feishu_drive(action: str, params: dict = {}) -> dict | list:
    """Dispatch Drive operations by action name.

    Args:
        action: One of list, search, create_folder, move
        params: Action-specific parameters (see description)
    """
    api = _require_api()

    if action == "list":
        folder_token = params.get("folder_token", "")
        page_size = params.get("page_size", 20)
        return await api.list_drive_files(folder_token, page_size)

    elif action == "search":
        query = params.get("query", "")
        count = params.get("count", 10)
        return await api.search_drive(query, count)

    elif action == "create_folder":
        name = params.get("name", "")
        parent_token = params.get("parent_token", "")
        return await api.create_drive_folder(name, parent_token)

    elif action == "move":
        file_token = params.get("file_token", "")
        target_folder_token = params.get("target_folder_token", "")
        return await api.move_drive_file(file_token, target_folder_token)

    elif action == "delete":
        file_token = params.get("file_token", "")
        file_type = params.get("file_type", "docx")
        return await api.delete_drive_file(file_token, file_type)

    else:
        return {"error": f"Unknown action: {action}"}
