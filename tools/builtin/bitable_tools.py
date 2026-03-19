# -*- coding: utf-8 -*-
"""Built-in Bitable (multidimensional table) tools.

Provides record-level CRUD for Feishu Bitable.
Requires a configured FeishuAPI instance set via `configure(api)`.
"""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.bitable")

_api: Any = None


def configure(api: Any) -> None:
    """Set the Feishu API client."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for bitable tools")
    return _api


@tool(description="List tables in a Bitable app", parallel_safe=True)
async def list_bitable_tables(app_token: str) -> list:
    """List all tables in a Bitable app.

    Args:
        app_token: The Bitable app token (from URL)
    """
    api = _require_api()
    return await api.list_bitable_tables(app_token)


@tool(description="Query records from a Bitable table", parallel_safe=True)
async def query_bitable_records(
    app_token: str,
    table_id: str,
    filter_expr: str = "",
    page_size: int = 20,
) -> list:
    """Query records from a Bitable table with optional filter.

    Args:
        app_token: The Bitable app token
        table_id: The table ID
        filter_expr: Filter expression (e.g., 'AND(CurrentValue.[Status]="Active")')
        page_size: Max records to return (default 20)
    """
    api = _require_api()
    return await api.query_bitable_records(
        app_token, table_id, filter_expr=filter_expr, page_size=page_size
    )


@tool(description="Add a record to a Bitable table", parallel_safe=False)
async def add_bitable_record(
    app_token: str,
    table_id: str,
    fields: dict,
) -> dict:
    """Add a new record to a Bitable table.

    Args:
        app_token: The Bitable app token
        table_id: The table ID
        fields: Field values as a dict (e.g., {"Name": "test", "Status": "Active"})
    """
    api = _require_api()
    return await api.add_bitable_record(app_token, table_id, fields)


@tool(description="Update a Bitable record", parallel_safe=False)
async def update_bitable_record(
    app_token: str,
    table_id: str,
    record_id: str,
    fields: dict,
) -> dict:
    """Update fields of an existing Bitable record.

    Args:
        app_token: The Bitable app token
        table_id: The table ID
        record_id: The record ID to update
        fields: Field values to update
    """
    api = _require_api()
    return await api.update_bitable_record(app_token, table_id, record_id, fields)


@tool(description="Delete a Bitable record", parallel_safe=False)
async def delete_bitable_record(
    app_token: str,
    table_id: str,
    record_id: str,
) -> dict:
    """Delete a record from a Bitable table.

    Args:
        app_token: The Bitable app token
        table_id: The table ID
        record_id: The record ID to delete
    """
    api = _require_api()
    return await api.delete_bitable_record(app_token, table_id, record_id)
