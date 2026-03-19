# -*- coding: utf-8 -*-
"""Built-in Feishu tools — document, task, and message operations.

All tools are decorated with @tool and auto-discovered by the ToolRegistry.
They require a configured FeishuAPI instance set via `configure(api)`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.feishu")

# Module-level API client — set by configure()
_api: Any = None


def configure(api: Any) -> None:
    """Set the Feishu API client for all tools in this module."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured — call feishu_tools.configure(api) first")
    return _api


# ── Document tools ────────────────────────────────────────────────


@tool(description="Create a new Feishu document", parallel_safe=False)
async def create_document(title: str, folder_token: str = "") -> dict:
    """Create a new Feishu document.

    Args:
        title: Document title
        folder_token: Parent folder token (optional, creates in root if empty)
    """
    api = _require_api()
    result = await api.create_document(title=title, folder_token=folder_token)
    return result


@tool(description="Read content from a Feishu document", parallel_safe=True)
async def read_document(document_id: str) -> str:
    """Read the content of a Feishu document.

    Args:
        document_id: The document ID (from URL or create_document)
    """
    api = _require_api()
    content = await api.get_document_content(document_id)
    return content


@tool(description="Append markdown content to a Feishu document", parallel_safe=False)
async def append_document(document_id: str, content: str) -> dict:
    """Append markdown content to an existing Feishu document.

    Args:
        document_id: The document ID
        content: Markdown content to append
    """
    api = _require_api()
    result = await api.append_document(document_id, content)
    return result


@tool(description="Search Feishu documents by keyword", parallel_safe=True)
async def search_documents(query: str, count: int = 10) -> list:
    """Search Feishu documents.

    Args:
        query: Search keyword
        count: Max results to return (default 10)
    """
    api = _require_api()
    results = await api.search_documents(query, count=count)
    return results


@tool(description="List comments on a Feishu document", parallel_safe=True)
async def list_comments(document_id: str) -> list:
    """List comments on a Feishu document.

    Args:
        document_id: The document ID
    """
    api = _require_api()
    result = await api.list_comments(document_id)
    return result


@tool(description="Reply to a comment on a Feishu document", parallel_safe=False)
async def reply_comment(document_id: str, comment_id: str, content: str) -> dict:
    """Reply to a comment on a Feishu document.

    Args:
        document_id: The document ID
        comment_id: The comment ID to reply to
        content: Reply text content
    """
    api = _require_api()
    result = await api.reply_comment(document_id, comment_id, content)
    return result


@tool(description="Replace a specific section in a Feishu document (heading to next same-level heading)", parallel_safe=False)
async def replace_section(document_id: str, heading_title: str, new_content: str) -> dict:
    """Replace a document section identified by its heading title.

    Deletes all blocks from the matched heading to the next heading of the same
    or higher level, then appends new_content to the document.

    Args:
        document_id: The document ID
        heading_title: Exact text of the heading to match
        new_content: Markdown content to replace the section with
    """
    api = _require_api()
    result = await api.replace_section(document_id, heading_title, new_content)
    return result


@tool(description="Update a Feishu document (replace all content)", parallel_safe=False)
async def update_document(document_id: str, content: str) -> dict:
    """Update a Feishu document by replacing all content.

    Args:
        document_id: The document ID
        content: New markdown content
    """
    api = _require_api()
    result = await api.update_document(document_id, content)
    return result


@tool(description="Transfer ownership of a Feishu document", parallel_safe=False)
async def transfer_document_owner(document_id: str, new_owner_id: str) -> dict:
    """Transfer ownership of a Feishu document.

    Args:
        document_id: The document ID
        new_owner_id: The new owner's user ID (open_id)
    """
    api = _require_api()
    result = await api.transfer_document_owner(document_id, new_owner_id)
    return result


@tool(description="List files in a Feishu Drive folder", parallel_safe=True)
async def list_folder(folder_token: str = "") -> list:
    """List files in a Feishu Drive folder.

    Args:
        folder_token: Folder token (empty for root)
    """
    api = _require_api()
    result = await api.list_drive_files(folder_token=folder_token)
    return result


# ── Task tools ────────────────────────────────────────────────────


@tool(description="Create a Feishu task", parallel_safe=False)
async def create_task(title: str, due_date: str = "", description: str = "") -> dict:
    """Create a new Feishu task.

    Args:
        title: Task title
        due_date: Due date in ISO format (e.g., 2025-12-31)
        description: Optional task description
    """
    api = _require_api()
    result = await api.create_task(title=title, due_date=due_date, description=description)
    return result


@tool(description="List Feishu tasks", parallel_safe=True)
async def list_tasks(completed: bool = False) -> list:
    """List Feishu tasks.

    Args:
        completed: If True, include completed tasks
    """
    api = _require_api()
    tasks = await api.list_tasks(completed=completed)
    return tasks


@tool(description="Complete a Feishu task", parallel_safe=False)
async def complete_task(task_id: str) -> dict:
    """Mark a Feishu task as completed.

    Args:
        task_id: The task GUID
    """
    api = _require_api()
    result = await api.complete_task(task_id)
    return result


@tool(description="Get details of a Feishu task", parallel_safe=True)
async def get_task(task_id: str) -> dict:
    """Get details of a Feishu task.

    Args:
        task_id: The task GUID
    """
    api = _require_api()
    result = await api.get_task(task_id)
    return result


@tool(description="Update a Feishu task", parallel_safe=False)
async def update_task(
    task_id: str, title: str = "", due_date: str = "", description: str = ""
) -> dict:
    """Update a Feishu task.

    Args:
        task_id: The task GUID
        title: New title (empty to keep current)
        due_date: New due date in ISO format (empty to keep current)
        description: New description (empty to keep current)
    """
    api = _require_api()
    result = await api.update_task(
        task_id=task_id, title=title, due_date=due_date, description=description
    )
    return result


@tool(description="Delete a Feishu task", parallel_safe=False)
async def delete_task(task_id: str) -> dict:
    """Delete a Feishu task.

    Args:
        task_id: The task GUID
    """
    api = _require_api()
    result = await api.delete_task(task_id)
    return result


@tool(description="Assign users to a Feishu task", parallel_safe=False)
async def assign_task(task_id: str, open_ids: str) -> dict:
    """Assign one or more users to a task.

    Args:
        task_id: The task GUID
        open_ids: Comma-separated list of user open_ids
    """
    api = _require_api()
    ids = [uid.strip() for uid in open_ids.split(",") if uid.strip()]
    result = await api.assign_task(task_id, ids)
    return result


@tool(description="Unassign users from a Feishu task", parallel_safe=False)
async def unassign_task(task_id: str, open_ids: str) -> dict:
    """Remove one or more users from a task.

    Args:
        task_id: The task GUID
        open_ids: Comma-separated list of user open_ids
    """
    api = _require_api()
    ids = [uid.strip() for uid in open_ids.split(",") if uid.strip()]
    result = await api.unassign_task(task_id, ids)
    return result


@tool(description="Create a section in the Feishu bot tasklist", parallel_safe=False)
async def create_section(name: str) -> dict:
    """Create a section in the bot's tasklist.

    Args:
        name: Section name
    """
    api = _require_api()
    result = await api.create_task_section(name)
    return result


@tool(description="List sections in the Feishu bot tasklist", parallel_safe=True)
async def list_sections() -> list:
    """List all sections in the bot's tasklist."""
    api = _require_api()
    result = await api.list_task_sections()
    return result


@tool(description="Get a categorized snapshot of all open Feishu tasks", parallel_safe=True)
async def task_snapshot() -> str:
    """Get a snapshot of all open tasks, categorized by: overdue, due soon, open."""
    api = _require_api()
    result = await api.task_snapshot()
    return result


# ── Message tools ─────────────────────────────────────────────────


@tool(description="Send a message to a Feishu chat", parallel_safe=False)
async def send_message(chat_id: str, text: str) -> dict:
    """Send a text message to a Feishu chat.

    Args:
        chat_id: Target chat ID
        text: Message text (markdown supported)
    """
    api = _require_api()
    result = await api.send_message(chat_id, text)
    return result


# ── Calendar tools ────────────────────────────────────────────────


@tool(description="List upcoming calendar events", parallel_safe=True)
async def list_events(days: int = 7) -> list:
    """List upcoming calendar events.

    Args:
        days: Number of days ahead to look (default 7)
    """
    api = _require_api()
    events = await api.list_events(days=days)
    return events


@tool(description="Create a calendar event", parallel_safe=False)
async def create_event(
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
) -> dict:
    """Create a calendar event.

    Args:
        summary: Event title
        start_time: Start time in ISO format
        end_time: End time in ISO format
        description: Optional event description
    """
    api = _require_api()
    result = await api.create_event(
        summary=summary,
        start_time=start_time,
        end_time=end_time,
        description=description,
    )
    return result


@tool(description="Update a Feishu calendar event", parallel_safe=False)
async def update_event(
    event_id: str,
    summary: str = "",
    start_time: str = "",
    end_time: str = "",
    description: str = "",
) -> dict:
    """Update a calendar event.

    Args:
        event_id: The event ID to update
        summary: New event title (empty to keep current)
        start_time: New start time in ISO format (empty to keep current)
        end_time: New end time in ISO format (empty to keep current)
        description: New description (empty to keep current)
    """
    api = _require_api()
    result = await api.update_event(
        event_id=event_id,
        summary=summary,
        start_time=start_time,
        end_time=end_time,
        description=description,
    )
    return result


@tool(description="Query free/busy status for a time range", parallel_safe=True)
async def freebusy(
    start_time: str,
    end_time: str,
    user_ids: str = "",
) -> dict:
    """Query free/busy status for a time range.

    Args:
        start_time: Start time (ISO format, e.g., 2025-12-31T09:00:00)
        end_time: End time (ISO format, e.g., 2025-12-31T18:00:00)
        user_ids: Comma-separated open_ids to query (empty for bot's own calendar)
    """
    api = _require_api()
    ids = [uid.strip() for uid in user_ids.split(",") if uid.strip()] if user_ids else None
    result = await api.freebusy(start_time=start_time, end_time=end_time, user_ids=ids)
    return result


@tool(description="Delete a Feishu calendar event", parallel_safe=False)
async def delete_event(event_id: str) -> dict:
    """Delete a calendar event.

    Args:
        event_id: The event ID to delete
    """
    api = _require_api()
    result = await api.delete_event(event_id=event_id)
    return result
