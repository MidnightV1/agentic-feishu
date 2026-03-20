# -*- coding: utf-8 -*-
"""Built-in Permission tools — document access control."""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.perm")

_api: Any = None


def configure(api: Any) -> None:
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for perm tools")
    return _api


@tool(description="Add a collaborator to a document. Do NOT modify permissions unless user explicitly requests it.", parallel_safe=False)
async def add_collaborator(
    doc_token: str,
    member_id: str,
    perm: str = "full_access",
    member_type: str = "openid",
    doc_type: str = "docx",
) -> dict:
    """Add a collaborator to a Feishu document.

    Args:
        doc_token: Document token
        member_id: User open_id or department_id
        perm: Permission level ("full_access" or "view")
        member_type: "openid" or "departmentid"
        doc_type: Document type ("docx", "sheet", "bitable", etc.)
    """
    api = _require_api()
    return await api.add_collaborator(doc_token, member_id, perm, member_type, doc_type)


@tool(description="Remove a collaborator from a document. IRREVERSIBLE — confirm with user. Do NOT modify unless explicitly requested.", parallel_safe=False)
async def remove_collaborator(
    doc_token: str,
    member_id: str,
    member_type: str = "openid",
    doc_type: str = "docx",
) -> dict:
    """Remove a collaborator from a Feishu document.

    Args:
        doc_token: Document token
        member_id: User open_id to remove
        member_type: "openid" or "departmentid"
        doc_type: Document type
    """
    api = _require_api()
    return await api.remove_collaborator(doc_token, member_id, member_type, doc_type)


@tool(description="Set document public sharing level. Do NOT modify unless user explicitly requests it. Explain current state when sharing.", parallel_safe=False)
async def set_public_sharing(
    doc_token: str,
    link_share_entity: str = "tenant_readable",
    doc_type: str = "docx",
) -> dict:
    """Set the public sharing level for a document.

    Args:
        doc_token: Document token
        link_share_entity: "anyone_readable", "anyone_editable", "tenant_readable", "tenant_editable", "closed" (to disable sharing)
        doc_type: Document type
    """
    api = _require_api()
    return await api.set_public_sharing(doc_token, link_share_entity, doc_type)
