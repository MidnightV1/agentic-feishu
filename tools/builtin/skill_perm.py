# -*- coding: utf-8 -*-
"""Skill-style Permission meta-tool — consolidates all document permission operations into one tool."""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.skill_perm")

_api: Any = None


def configure(api: Any) -> None:
    """Set the Feishu API client."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for skill_perm")
    return _api


@tool(description="""Feishu document permission operations.

Actions:
- list: List collaborators. params: {doc_token, doc_type?="docx"}
- get_sharing: Get public sharing settings. params: {doc_token, doc_type?="docx"}
- add: Add collaborator. params: {doc_token, member_id, perm?="full_access", member_type?="openid", doc_type?="docx"}
- remove: Remove collaborator (IRREVERSIBLE). params: {doc_token, member_id, member_type?="openid", doc_type?="docx"}
- set_sharing: Set link sharing. params: {doc_token, link_share_entity?="tenant_readable", doc_type?="docx"}. Values: "anyone_readable", "anyone_editable", "tenant_readable", "tenant_editable", "closed"
""", parallel_safe=False)
async def feishu_perm(action: str, params: dict = {}) -> dict | list:
    """Dispatch permission operations by action name.

    Args:
        action: One of list, get_sharing, add, remove, set_sharing
        params: Action-specific parameters (see description)
    """
    api = _require_api()

    if action == "list":
        doc_token = params.get("doc_token", "")
        doc_type = params.get("doc_type", "docx")
        return await api.list_collaborators(doc_token, doc_type)

    elif action == "get_sharing":
        doc_token = params.get("doc_token", "")
        doc_type = params.get("doc_type", "docx")
        return await api.get_public_sharing(doc_token, doc_type)

    elif action == "add":
        doc_token = params.get("doc_token", "")
        member_id = params.get("member_id", "")
        perm = params.get("perm", "full_access")
        member_type = params.get("member_type", "openid")
        doc_type = params.get("doc_type", "docx")
        return await api.add_collaborator(doc_token, member_id, perm, member_type, doc_type)

    elif action == "remove":
        doc_token = params.get("doc_token", "")
        member_id = params.get("member_id", "")
        member_type = params.get("member_type", "openid")
        doc_type = params.get("doc_type", "docx")
        return await api.remove_collaborator(doc_token, member_id, member_type, doc_type)

    elif action == "set_sharing":
        doc_token = params.get("doc_token", "")
        link_share_entity = params.get("link_share_entity", "tenant_readable")
        doc_type = params.get("doc_type", "docx")
        return await api.set_public_sharing(doc_token, link_share_entity, doc_type)

    else:
        return {"error": f"Unknown action: {action}"}
