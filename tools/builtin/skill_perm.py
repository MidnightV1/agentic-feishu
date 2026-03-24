# -*- coding: utf-8 -*-
"""Skill-style Permission meta-tool — consolidates all document permission operations into one tool."""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool
from tools.builtin._user_context import resolve_user
from tools.common.url_parser import extract_token
from tools.common.validators import validate_required, validate_enum, validate_action, VALID_PERM_TYPES

log = logging.getLogger("agentic.tools.skill_perm")

_api: Any = None

PERM_ACTIONS = {"list", "get_sharing", "add", "remove", "set_sharing"}


def configure(api: Any) -> None:
    """Set the Feishu API client."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured for skill_perm")
    return _api


@tool(deferred=True, parallel_safe=False)
async def feishu_perm(action: str, params: dict = {}) -> dict | list:
    """Dispatch permission operations by action name.

    Args:
        action: One of list, get_sharing, add, remove, set_sharing
        params: Action-specific parameters (see description)
    """
    api = _require_api()
    validate_action(action, PERM_ACTIONS, "feishu_perm")

    # Auto-extract token from Feishu URLs
    if "doc_token" in params and params["doc_token"]:
        params["doc_token"] = extract_token(params["doc_token"])

    if action == "list":
        validate_required(params, ["doc_token"])
        doc_token = params.get("doc_token", "")
        doc_type = params.get("doc_type", "docx")
        return await api.list_collaborators(doc_token, doc_type)

    elif action == "get_sharing":
        validate_required(params, ["doc_token"])
        doc_token = params.get("doc_token", "")
        doc_type = params.get("doc_type", "docx")
        return await api.get_public_sharing(doc_token, doc_type)

    elif action == "add":
        validate_required(params, ["doc_token", "member_id"])
        validate_enum(params.get("perm", "full_access"), VALID_PERM_TYPES, "perm")
        doc_token = params.get("doc_token", "")
        member_id = resolve_user(params.get("member_id", ""))
        perm = params.get("perm", "full_access")
        member_type = params.get("member_type", "openid")
        doc_type = params.get("doc_type", "docx")
        return await api.add_collaborator(doc_token, member_id, perm, member_type, doc_type)

    elif action == "remove":
        validate_required(params, ["doc_token", "member_id"])
        doc_token = params.get("doc_token", "")
        member_id = resolve_user(params.get("member_id", ""))
        member_type = params.get("member_type", "openid")
        doc_type = params.get("doc_type", "docx")
        return await api.remove_collaborator(doc_token, member_id, member_type, doc_type)

    elif action == "set_sharing":
        validate_required(params, ["doc_token"])
        doc_token = params.get("doc_token", "")
        link_share_entity = params.get("link_share_entity", "tenant_readable")
        doc_type = params.get("doc_type", "docx")
        return await api.set_public_sharing(doc_token, link_share_entity, doc_type)
