# -*- coding: utf-8 -*-
"""Built-in task tool — consolidates all Feishu task operations into a single feishu_task tool."""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.task")

_api: Any = None


def configure(api: Any) -> None:
    """Set the Feishu API client for all tools in this module."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured — call skill_task.configure(api) first")
    return _api


@tool(
    summary="Feishu task operations: create, get, list, update, complete, delete, assign, unassign, snapshot",
    deferred=True,
    description="""Feishu task operations.

Actions:
- create: Create task. params: {title, due_date?, description?}
  Due date auto-converts from ISO/natural format. Infer from context (e.g., '下周五' → concrete date).
- get: Get task details. params: {task_id}
- list: List tasks. params: {completed?=false}
- update: Update task. params: {task_id, title?, due_date?, description?}
- complete: Mark complete. params: {task_id}
- delete: Delete task (IRREVERSIBLE — confirm with user first). params: {task_id}
- assign: Assign users. params: {task_id, open_ids} (comma-separated open_ids string)
- unassign: Unassign users. params: {task_id, open_ids} (comma-separated open_ids string)
- snapshot: Get categorized overview of all open tasks. No params needed.
""", parallel_safe=False,
)
async def feishu_task(action: str, params: dict = {}) -> dict | str | list:
    """Dispatch Feishu task operations by action name.

    Args:
        action: One of: create, get, list, update, complete, delete, assign, unassign, snapshot
        params: Action-specific parameters (see description)
    """
    api = _require_api()

    if action == "create":
        result = await api.create_task(
            title=params["title"],
            due_date=params.get("due_date", ""),
            description=params.get("description", ""),
        )
        return result

    elif action == "get":
        result = await api.get_task(params["task_id"])
        return result

    elif action == "list":
        tasks = await api.list_tasks(completed=params.get("completed", False))
        return tasks

    elif action == "update":
        result = await api.update_task(
            task_id=params["task_id"],
            title=params.get("title", ""),
            due_date=params.get("due_date", ""),
            description=params.get("description", ""),
        )
        return result

    elif action == "complete":
        result = await api.complete_task(params["task_id"])
        return result

    elif action == "delete":
        result = await api.delete_task(params["task_id"])
        return result

    elif action == "assign":
        ids = [uid.strip() for uid in params["open_ids"].split(",") if uid.strip()]
        result = await api.assign_task(params["task_id"], ids)
        return result

    elif action == "unassign":
        ids = [uid.strip() for uid in params["open_ids"].split(",") if uid.strip()]
        result = await api.unassign_task(params["task_id"], ids)
        return result

    elif action == "snapshot":
        result = await api.task_snapshot()
        return result

    else:
        raise ValueError(
            f"Unknown action '{action}'. Valid actions: create, get, list, update, complete, delete, assign, unassign, snapshot"
        )
