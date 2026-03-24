"""Shared permission operation helpers, used as post-action callbacks by doc/bitable/sheet/drive."""

import logging
from typing import Any

log = logging.getLogger("agentic.perm_ops")


async def ensure_user_access(
    api: Any,
    token: str,
    file_type: str,
    user_id: str,
    perm: str = "full_access",
    member_type: str = "openid",
) -> dict:
    """确保用户对指定文档/文件有权限。

    这是一个 post-action 函数，被创建类操作自动调用。
    如果添加失败，只 warning 不 raise（不阻断主操作）。

    Args:
        api: FeishuAPI instance
        token: 文档/文件 token
        file_type: docx, sheet, bitable, folder 等
        user_id: 用户 open_id
        perm: 权限级别（full_access / edit / view / comment）
        member_type: 成员类型（openid / userid / chatid / departmentid）

    Returns:
        {"success": True/False, "error": str?}
    """
    try:
        await api.add_collaborator(token, user_id, perm, member_type, file_type)
        return {"success": True}
    except Exception as e:
        log.warning("ensure_user_access failed for %s/%s: %s", file_type, token, e)
        return {"success": False, "error": str(e)}
