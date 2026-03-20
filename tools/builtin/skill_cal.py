# -*- coding: utf-8 -*-
"""Feishu calendar skill — consolidated tool for all calendar operations.

All calendar actions are dispatched through a single `feishu_cal` entry point.
Requires a configured FeishuAPI instance set via `configure(api)`.
"""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.skill_cal")

# Module-level API client — set by configure()
_api: Any = None


def configure(api: Any) -> None:
    """Set the Feishu API client for all tools in this module."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured — call skill_cal.configure(api) first")
    return _api


@tool(
    summary="Feishu calendar operations: create, list, update, delete, freebusy",
    deferred=True,
    description="""Feishu calendar operations.

Actions:
- create: Create event. params: {summary, start_time, end_time, description?, attendees?}. MUST add requesting user as attendee (open_id from message context) so event appears in their calendar. MUST inform user after creation (title + time).
- list: List upcoming events. params: {days?=7}
- update: Update event. params: {event_id, summary?, start_time?, end_time?, description?}
- delete: Delete event (IRREVERSIBLE — confirm with user first). params: {event_id}
- freebusy: Check free/busy status. params: {start_time, end_time, user_ids?} (user_ids is comma-separated open_id string, empty for bot's own calendar)
""", parallel_safe=False,
)
async def feishu_cal(action: str, params: dict = {}) -> dict | list:
    """Unified Feishu calendar tool.

    Args:
        action: One of: create, list, update, delete, freebusy
        params: Action-specific parameters (see description)
    """
    api = _require_api()

    if action == "create":
        attendees = params.get("attendees")
        # Normalize attendees: accept list of strings or list of dicts
        if attendees is not None:
            normalized = []
            for item in attendees:
                if isinstance(item, str):
                    normalized.append(item)
                elif isinstance(item, dict):
                    # Extract open_id from dict if present
                    normalized.append(item.get("open_id", item))
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
        result = await api.update_event(
            event_id=params["event_id"],
            summary=params.get("summary", ""),
            start_time=params.get("start_time", ""),
            end_time=params.get("end_time", ""),
            description=params.get("description", ""),
        )
        return result

    elif action == "delete":
        result = await api.delete_event(event_id=params["event_id"])
        return result

    elif action == "freebusy":
        raw_ids = params.get("user_ids", "")
        ids = [uid.strip() for uid in raw_ids.split(",") if uid.strip()] if raw_ids else None
        result = await api.freebusy(
            start_time=params["start_time"],
            end_time=params["end_time"],
            user_ids=ids,
        )
        return result

    else:
        raise ValueError(f"Unknown feishu_cal action: {action!r}. Valid actions: create, list, update, delete, freebusy")
