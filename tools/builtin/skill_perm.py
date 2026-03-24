# -*- coding: utf-8 -*-
"""Feishu Permission operations — CLI script for skill invocation.

Called by LLM via bash tool. No @tool registration.

CLI usage:
    python3 tools/builtin/skill_perm.py <action> --params '<json>'
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

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


# -- CLI entry point ---------------------------------------------------------

async def _cli_main() -> None:
    """CLI entry: python3 skill_perm.py <action> --params '<json>'"""
    import argparse
    from config.settings import load_settings
    from platforms.feishu.api import FeishuAPI

    parser = argparse.ArgumentParser(description="Feishu Permission operations")
    parser.add_argument("action", choices=sorted(PERM_ACTIONS))
    parser.add_argument("--params", default="{}", help="JSON params")
    args = parser.parse_args()

    params = json.loads(args.params)

    settings = load_settings()
    api = FeishuAPI(app_id=settings.feishu.app_id, app_secret=settings.feishu.app_secret)
    await api.start()
    configure(api)

    try:
        result = await feishu_perm(args.action, params)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        await api.stop()


if __name__ == "__main__":
    asyncio.run(_cli_main())
