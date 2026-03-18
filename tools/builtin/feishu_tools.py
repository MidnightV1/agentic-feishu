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
