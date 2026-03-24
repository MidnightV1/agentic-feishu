# -*- coding: utf-8 -*-
"""Feishu task operations — CLI script for skill invocation.

Called by LLM via bash tool. No @tool registration.

CLI usage:
    python3 tools/builtin/skill_task.py <action> --params '<json>'
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from tools.builtin._user_context import resolve_user
from tools.common.validators import validate_required, validate_action

log = logging.getLogger("agentic.tools.task")

_api: Any = None

TASK_ACTIONS = {"create", "get", "list", "update", "complete", "delete", "assign", "unassign", "snapshot"}


def configure(api: Any) -> None:
    """Set the Feishu API client for all tools in this module."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured — call skill_task.configure(api) first")
    return _api


async def feishu_task(action: str, params: dict = {}) -> dict | str | list:
    """Dispatch Feishu task operations by action name.

    Args:
        action: One of: create, get, list, update, complete, delete, assign, unassign, snapshot
        params: Action-specific parameters (see description)
    """
    api = _require_api()
    validate_action(action, TASK_ACTIONS, "feishu_task")

    if action == "create":
        validate_required(params, ["title"])
        result = await api.create_task(
            title=params["title"],
            due_date=params.get("due_date", ""),
            description=params.get("description", ""),
        )
        return result

    elif action == "get":
        validate_required(params, ["task_id"])
        result = await api.get_task(params["task_id"])
        return result

    elif action == "list":
        tasks = await api.list_tasks(completed=params.get("completed", False))
        return tasks

    elif action == "update":
        validate_required(params, ["task_id"])
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
        validate_required(params, ["task_id"])
        if not params.get("confirmed"):
            return {
                "status": "confirmation_required",
                "message": f"删除任务不可恢复。确认删除任务 {params['task_id']}？请重新调用并设置 confirmed=true",
                "action": action,
                "params": params,
            }
        result = await api.delete_task(params["task_id"])
        return result

    elif action == "assign":
        validate_required(params, ["task_id", "open_ids"])
        ids = [resolve_user(uid.strip()) for uid in params["open_ids"].split(",") if uid.strip()]
        result = await api.assign_task(params["task_id"], ids)
        return result

    elif action == "unassign":
        validate_required(params, ["task_id", "open_ids"])
        ids = [resolve_user(uid.strip()) for uid in params["open_ids"].split(",") if uid.strip()]
        result = await api.unassign_task(params["task_id"], ids)
        return result

    elif action == "snapshot":
        result = await api.task_snapshot()
        return result


# -- CLI entry point ---------------------------------------------------------

async def _cli_main() -> None:
    """CLI entry: python3 skill_task.py <action> --params '<json>'"""
    import argparse
    from config.settings import load_settings
    from platforms.feishu.api import FeishuAPI

    parser = argparse.ArgumentParser(description="Feishu task operations")
    parser.add_argument("action", choices=sorted(TASK_ACTIONS))
    parser.add_argument("--params", default="{}", help="JSON params")
    args = parser.parse_args()

    params = json.loads(args.params)

    settings = load_settings()
    api = FeishuAPI(app_id=settings.feishu.app_id, app_secret=settings.feishu.app_secret)
    await api.start()
    configure(api)

    try:
        result = await feishu_task(args.action, params)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        await api.stop()


if __name__ == "__main__":
    asyncio.run(_cli_main())
