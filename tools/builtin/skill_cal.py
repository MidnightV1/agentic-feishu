# -*- coding: utf-8 -*-
"""Feishu calendar skill — consolidated tool for all calendar operations.

All calendar actions are dispatched through a single `feishu_cal` entry point.
Requires a configured FeishuAPI instance set via `configure(api)`.
"""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool
from tools.builtin._user_context import resolve_user
from tools.common.validators import validate_required, validate_action

log = logging.getLogger("agentic.tools.skill_cal")

# Module-level API client — set by configure()
_api: Any = None

CAL_ACTIONS = {
    "create", "list", "update", "delete", "freebusy",
    "list_attendees", "add_attendees", "remove_attendees",
}


def configure(api: Any) -> None:
    """Set the Feishu API client for all tools in this module."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured — call skill_cal.configure(api) first")
    return _api


@tool(deferred=True, parallel_safe=False)
async def feishu_cal(action: str, params: dict = {}) -> dict | list:
    """Unified Feishu calendar tool.

    Args:
        action: One of: create, list, update, delete, freebusy, list_attendees, add_attendees, remove_attendees
        params: Action-specific parameters (see description)
    """
    api = _require_api()
    validate_action(action, CAL_ACTIONS, "feishu_cal")

    if action == "create":
        validate_required(params, ["summary", "start_time", "end_time"])
        attendees = params.get("attendees")
        # Normalize attendees: accept names, open_ids, or dicts
        if attendees is not None:
            normalized = []
            for item in attendees:
                if isinstance(item, str):
                    normalized.append(resolve_user(item))
                elif isinstance(item, dict):
                    normalized.append(resolve_user(item.get("open_id", "")))
                else:
                    normalized.append(item)
            attendees = normalized if normalized else None

        result = await api.create_event(
            summary=params["summary"],
            start_time=params["start_time"],
            end_time=params["end_time"],
            description=params.get("description", ""),
            attendees=attendees,
        )
        return result

    elif action == "list":
        days = params.get("days", 7)
        events = await api.list_events(days=days)
        return events

    elif action == "update":
        validate_required(params, ["event_id"])
        result = await api.update_event(
            event_id=params["event_id"],
            summary=params.get("summary", ""),
            start_time=params.get("start_time", ""),
            end_time=params.get("end_time", ""),
            description=params.get("description", ""),
        )
        return result

    elif action == "delete":
        validate_required(params, ["event_id"])
        if not params.get("confirmed"):
            return {
                "status": "confirmation_required",
                "message": f"删除日历事件不可恢复。确认删除事件 {params['event_id']}？请重新调用并设置 confirmed=true",
                "action": action,
                "params": params,
            }
        result = await api.delete_event(event_id=params["event_id"])
        return result

    elif action == "freebusy":
        validate_required(params, ["start_time", "end_time"])
        raw_ids = params.get("user_ids", "")
        ids = [resolve_user(uid.strip()) for uid in raw_ids.split(",") if uid.strip()] if raw_ids else None
        result = await api.freebusy(
            start_time=params["start_time"],
            end_time=params["end_time"],
            user_ids=ids,
        )
        return result

    elif action == "list_attendees":
        validate_required(params, ["event_id"])
        return await api.list_event_attendees(params["event_id"])

    elif action == "add_attendees":
        validate_required(params, ["event_id", "attendee_ids"])
        raw_ids = params.get("attendee_ids", "")
        ids = [resolve_user(uid.strip()) for uid in raw_ids.split(",") if uid.strip()] if isinstance(raw_ids, str) else [resolve_user(uid) for uid in raw_ids]
        return await api.add_event_attendees(params["event_id"], ids)

    elif action == "remove_attendees":
        validate_required(params, ["event_id", "attendee_ids"])
        raw_ids = params.get("attendee_ids", "")
        ids = [uid.strip() for uid in raw_ids.split(",") if uid.strip()] if isinstance(raw_ids, str) else raw_ids
        return await api.remove_event_attendees(params["event_id"], ids)
