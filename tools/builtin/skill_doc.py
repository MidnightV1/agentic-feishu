# -*- coding: utf-8 -*-
"""Consolidated Feishu document tool — all document operations via a single dispatch interface.

All tools are decorated with @tool and auto-discovered by the ToolRegistry.
Requires a configured FeishuAPI instance set via `configure(api)`.
"""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool
from tools.builtin._user_context import get_current_user_id
from tools.common.url_parser import extract_token
from tools.common.validators import validate_required, validate_action

log = logging.getLogger("agentic.tools.skill_doc")

# Module-level API client — set by configure()
_api: Any = None

DOC_ACTIONS = {
    "create", "read", "append", "update", "replace_section", "search",
    "list_comments", "analyze_comments", "reply_comment", "transfer_owner", "send_message",
}


def configure(api: Any) -> None:
    """Set the Feishu API client for all tools in this module."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured — call skill_doc.configure(api) first")
    return _api


@tool(deferred=True, parallel_safe=False)
async def feishu_doc(action: str, params: dict) -> dict:
    """Dispatch Feishu document operations by action name.

    Args:
        action: One of create, read, append, update, replace_section, search,
                list_comments, analyze_comments, reply_comment, transfer_owner, send_message
        params: Action-specific parameters (see tool description)
    """
    api = _require_api()
    validate_action(action, DOC_ACTIONS, "feishu_doc")

    # Auto-extract token from Feishu URLs for all doc_id-bearing actions
    if "document_id" in params and params["document_id"]:
        params["document_id"] = extract_token(params["document_id"])

    if action == "create":
        validate_required(params, ["title"])
        if not params.get("force"):
            title = params["title"]
            existing = await api.search_documents(title, count=5)
            for doc in existing:
                if doc.get("title") == title:
                    return {
                        "status": "duplicate_found",
                        "message": f"已存在同名文档：{title}。确认要创建？请设置 force=true",
                        "existing": doc,
                    }
        result = await api.create_document(
            title=params.get("title", ""),
            folder_token=params.get("folder_token", ""),
        )
        from skills.feishu_perm.lib.perm_ops import ensure_user_access

        user_id = get_current_user_id()
        if user_id and result.get("document_id"):
            perm_result = await ensure_user_access(api, result["document_id"], "docx", user_id)
            result["auto_collaborator"] = user_id
            if not perm_result.get("success"):
                result["auto_collaborator_error"] = perm_result.get("error", "unknown")
        return result

    elif action == "read":
        validate_required(params, ["document_id"])
        content = await api.get_document_content(params["document_id"])
        return {"content": content}

    elif action == "append":
        validate_required(params, ["document_id", "content"])
        return await api.append_document(params["document_id"], params["content"])

    elif action == "update":
        validate_required(params, ["document_id", "content"])
        return await api.update_document(params["document_id"], params["content"])

    elif action == "replace_section":
        validate_required(params, ["document_id", "heading_title", "new_content"])
        return await api.replace_section(
            params["document_id"],
            params["heading_title"],
            params["new_content"],
        )

    elif action == "search":
        validate_required(params, ["query"])
        results = await api.search_documents(
            params.get("query", ""),
            count=params.get("count", 10),
        )
        return {"results": results}

    elif action == "list_comments":
        validate_required(params, ["document_id"])
        comments = await api.list_comments(params["document_id"])
        return {"comments": comments}

    elif action == "analyze_comments":
        validate_required(params, ["document_id"])
        return await api.analyze_comments(
            params["document_id"],
            show_all=params.get("show_all", False),
            context_chars=params.get("context_chars", 200),
        )

    elif action == "reply_comment":
        validate_required(params, ["document_id", "comment_id", "content"])
        return await api.reply_comment(
            params["document_id"],
            params["comment_id"],
            params["content"],
        )

    elif action == "transfer_owner":
        validate_required(params, ["document_id", "new_owner_id"])
        if not params.get("confirmed"):
            return {
                "status": "confirmation_required",
                "message": f"转交文档所有权不可逆。确认转交给 {params['new_owner_id']}？请重新调用并设置 confirmed=true",
                "action": action,
                "params": params,
            }
        return await api.transfer_document_owner(params["document_id"], params["new_owner_id"])

    elif action == "send_message":
        return await api.send_message(
            params.get("chat_id", ""),
            params.get("text", ""),
        )
