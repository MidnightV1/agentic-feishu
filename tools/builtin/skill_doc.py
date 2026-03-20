# -*- coding: utf-8 -*-
"""Consolidated Feishu document tool — all document operations via a single dispatch interface.

All tools are decorated with @tool and auto-discovered by the ToolRegistry.
Requires a configured FeishuAPI instance set via `configure(api)`.
"""

from __future__ import annotations

import logging
from typing import Any

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.skill_doc")

# Module-level API client — set by configure()
_api: Any = None


def configure(api: Any) -> None:
    """Set the Feishu API client for all tools in this module."""
    global _api
    _api = api


def _require_api() -> Any:
    if _api is None:
        raise RuntimeError("Feishu API not configured — call skill_doc.configure(api) first")
    return _api


@tool(description="""Feishu document operations.

Actions:
- create: Create document. params: {title, folder_token?}. MUST add requesting user as full_access collaborator after creation. Returns full link: https://feishu.cn/docx/{document_id}.
- read: Read document content. params: {document_id}
- append: Append markdown content to document. params: {document_id, content}. Do NOT create a new doc if one already exists on the same topic.
- update: Replace ALL content in document (destructive). params: {document_id, content}. Prefer append or replace_section for partial updates.
- replace_section: Replace a section identified by its heading. params: {document_id, heading_title, new_content}. Deletes from matched heading to next same-level heading, then inserts new_content.
- search: Search documents by keyword. params: {query, count?=10}. Use short keywords, not full sentences.
- list_comments: List comments on a document. params: {document_id}
- reply_comment: Reply to a comment. params: {document_id, comment_id, content}
- transfer_owner: Transfer document ownership (IRREVERSIBLE — confirm with user first). params: {document_id, new_owner_id}
- send_message: Send message to a Feishu chat. params: {chat_id, text}. Only when user EXPLICITLY requests it. Never proactively send to other chats.
""", parallel_safe=False)
async def feishu_doc(action: str, params: dict) -> dict:
    """Dispatch Feishu document operations by action name.

    Args:
        action: One of create, read, append, update, replace_section, search,
                list_comments, reply_comment, transfer_owner
        params: Action-specific parameters (see tool description)
    """
    api = _require_api()

    if action == "create":
        return await api.create_document(
            title=params.get("title", ""),
            folder_token=params.get("folder_token", ""),
        )

    elif action == "read":
        content = await api.get_document_content(params.get("document_id", ""))
        return {"content": content}

    elif action == "append":
        return await api.append_document(
            params.get("document_id", ""),
            params.get("content", ""),
        )

    elif action == "update":
        return await api.update_document(
            params.get("document_id", ""),
            params.get("content", ""),
        )

    elif action == "replace_section":
        return await api.replace_section(
            params.get("document_id", ""),
            params.get("heading_title", ""),
            params.get("new_content", ""),
        )

    elif action == "search":
        results = await api.search_documents(
            params.get("query", ""),
            count=params.get("count", 10),
        )
        return {"results": results}

    elif action == "list_comments":
        comments = await api.list_comments(params.get("document_id", ""))
        return {"comments": comments}

    elif action == "reply_comment":
        return await api.reply_comment(
            params.get("document_id", ""),
            params.get("comment_id", ""),
            params.get("content", ""),
        )

    elif action == "transfer_owner":
        return await api.transfer_document_owner(
            params.get("document_id", ""),
            params.get("new_owner_id", ""),
        )

    elif action == "send_message":
        return await api.send_message(
            params.get("chat_id", ""),
            params.get("text", ""),
        )

    else:
        return {"error": f"Unknown action: {action!r}. Valid actions: create, read, append, update, replace_section, search, list_comments, reply_comment, transfer_owner, send_message"}
