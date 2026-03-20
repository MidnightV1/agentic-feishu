# -*- coding: utf-8 -*-
"""Skill-style Bitable meta-tool — consolidates all Bitable operations into one tool."""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.skill_bitable")

_api: Any = None


def configure(api: Any) -> None:
    """Set the Feishu API client."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for skill_bitable")
    return _api


@tool(
    summary="Feishu Bitable operations: create, list_tables, query, add_record, update_record, delete_record",
    deferred=True,
    description="""Feishu Bitable (multidimensional table) operations.

Actions:
- create: Create Bitable app. params: {name, folder_token?}. MUST add user as full_access after creation.
- list_tables: List tables in app. params: {app_token}
- query: Query records. params: {app_token, table_id, filter_expr?, page_size?=20}. Field names are CASE-SENSITIVE.
- add_record: Add record. params: {app_token, table_id, fields}
- update_record: Update record. params: {app_token, table_id, record_id, fields}
- delete_record: Delete record (IRREVERSIBLE). params: {app_token, table_id, record_id}
""", parallel_safe=False,
)
async def feishu_bitable(action: str, params: dict = {}) -> dict | list:
    """Dispatch Bitable operations by action name.

    Args:
        action: One of create, list_tables, query, add_record, update_record, delete_record
        params: Action-specific parameters (see description)
    """
    api = _require_api()

    if action == "create":
        name = params.get("name", "")
        folder_token = params.get("folder_token", "")
        return await api.create_bitable(name, folder_token)

    elif action == "list_tables":
        app_token = params.get("app_token", "")
        return await api.list_bitable_tables(app_token)

    elif action == "query":
        app_token = params.get("app_token", "")
        table_id = params.get("table_id", "")
        filter_expr = params.get("filter_expr", "")
        page_size = params.get("page_size", 20)
        return await api.query_bitable_records(
            app_token, table_id, filter_expr=filter_expr, page_size=page_size
        )

    elif action == "add_record":
        app_token = params.get("app_token", "")
        table_id = params.get("table_id", "")
        fields = params.get("fields", {})
        return await api.add_bitable_record(app_token, table_id, fields)

    elif action == "update_record":
        app_token = params.get("app_token", "")
        table_id = params.get("table_id", "")
        record_id = params.get("record_id", "")
        fields = params.get("fields", {})
        return await api.update_bitable_record(app_token, table_id, record_id, fields)

    elif action == "delete_record":
        app_token = params.get("app_token", "")
        table_id = params.get("table_id", "")
        record_id = params.get("record_id", "")
        return await api.delete_bitable_record(app_token, table_id, record_id)

    else:
        return {"error": f"Unknown action: {action}"}
