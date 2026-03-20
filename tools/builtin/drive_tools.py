# -*- coding: utf-8 -*-
"""Built-in Drive tools — file/folder browsing, search, management."""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.drive")

_api: Any = None


def configure(api: Any) -> None:
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for drive tools")
    return _api


@tool(description="List files and folders in a Feishu Drive folder", parallel_safe=True)
async def list_drive_files(folder_token: str = "", page_size: int = 20) -> list:
    """List contents of a Drive folder.

    Args:
        folder_token: Folder token (empty = root folder)
        page_size: Max items to return (default 20)
    """
    api = _require_api()
    return await api.list_drive_files(folder_token, page_size)


@tool(description="Search files in Feishu Drive", parallel_safe=True)
async def search_drive(query: str, count: int = 10) -> list:
    """Search for files by name in Feishu Drive.

    Args:
        query: Search keyword
        count: Max results (default 10)
    """
    api = _require_api()
    return await api.search_drive(query, count)


@tool(description="Create a folder in Feishu Drive. Search for existing folder first to avoid duplicates. parent_token is REQUIRED — use list_drive_files to find the target folder first.", parallel_safe=False)
async def create_drive_folder(name: str, parent_token: str) -> dict:
    """Create a new folder in Drive.

    Args:
        name: Folder name
        parent_token: Parent folder token (REQUIRED — get from list_drive_files)
    """
    api = _require_api()
    return await api.create_drive_folder(name, parent_token)


@tool(description="Move a file or folder to another folder. Confirm target path with user before executing.", parallel_safe=False)
async def move_drive_file(file_token: str, target_folder_token: str) -> dict:
    """Move a file or folder to another folder.

    Args:
        file_token: Token of the file/folder to move
        target_folder_token: Destination folder token
    """
    api = _require_api()
    return await api.move_drive_file(file_token, target_folder_token)
