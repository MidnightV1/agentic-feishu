"""Feishu workspace initialization — runs once at startup.

All operations are idempotent — safe to run on every restart.
"""

import logging
from typing import Any

log = logging.getLogger("agentic.init_workspace")

# 必需的飞书权限 scope（按 skill 分组）
REQUIRED_SCOPES = {
    "calendar": [
        "calendar:calendar",
        "calendar:calendar:readonly",
        "calendar:calendar.event:write",
    ],
    "task": [
        "task:task",
        "task:task:write",
    ],
    "drive": [
        "drive:drive",
        "drive:drive:readonly",
    ],
    "doc": [
        "docx:document",
        "docx:document:readonly",
    ],
    "perm": [
        "drive:drive:permission",
        "drive:drive:permission:readonly",
    ],
    "bitable": [
        "bitable:app",
        "bitable:app:readonly",
    ],
    "im": [
        "im:message",
        "im:message:send_as_bot",
    ],
}

# Task 默认 sections
DEFAULT_SECTIONS = ["紧急", "进行中", "待规划"]


async def initialize_workspace(api: Any, settings: Any) -> dict:
    """Initialize Feishu workspace resources. Idempotent.

    Args:
        api: FeishuAPI instance (already started)
        settings: Application settings

    Returns:
        Dict with initialization results
    """
    results = {}

    # 1. Task sections 初始化
    results["sections"] = await _init_task_sections(api)

    # 2. Scope 检查（advisory only, 不阻断启动）
    results["scope_check"] = _check_required_scopes()

    log.info(
        "Workspace initialization complete: %s",
        {k: "ok" if v.get("success", True) else "failed" for k, v in results.items()},
    )
    return results


async def _init_task_sections(api: Any) -> dict:
    """确保默认 task sections 存在。幂等操作。"""
    try:
        existing = await api.list_task_sections()
        existing_names = {s.get("name", "") for s in existing}
        created = []
        for name in DEFAULT_SECTIONS:
            if name not in existing_names:
                try:
                    await api.create_task_section(name)
                    created.append(name)
                    log.info("Created task section: %s", name)
                except Exception as e:
                    log.warning("Failed to create section '%s': %s", name, e)
        return {"success": True, "existing": list(existing_names), "created": created}
    except Exception as e:
        log.warning("Task section initialization failed: %s", e)
        return {"success": False, "error": str(e)}


def _check_required_scopes() -> dict:
    """Log required Feishu scopes for reference.

    Advisory only — we cannot programmatically verify authorized scopes.
    """
    all_scopes = []
    for group, scopes in REQUIRED_SCOPES.items():
        all_scopes.extend(scopes)
    log.info(
        "Required Feishu scopes (%d groups, %d total): %s",
        len(REQUIRED_SCOPES),
        len(all_scopes),
        ", ".join(REQUIRED_SCOPES.keys()),
    )
    return {"success": True, "scope_groups": list(REQUIRED_SCOPES.keys()), "total_scopes": len(all_scopes)}
