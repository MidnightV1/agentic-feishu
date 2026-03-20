# -*- coding: utf-8 -*-
"""XML tag protocol — input wrapping and output parsing for Feishu channel.

Tags serve as an attention-routing protocol: they tell the model which
information matters and route different parts of the output to different
channels (user-visible reply, async exploration, task orchestration).

Input side (wrapping before sending to LLM):
    <user-input>      — marks the actual user message (vs system noise)

Output side (parsing after LLM response):
    <reply-to-user>   — whitelist filter; only this content reaches the user
    <next-explore>    — exploration hints; processed async, never shown to user
    <task_plan>       — parallel task plan (JSON); intercepted by orchestrator
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


# ═══ Compiled patterns (greedy — grab outermost tags) ═══

_REPLY_TAG_RE = re.compile(r"<reply-to-user>(.*)</reply-to-user>", re.DOTALL)
_EXPLORE_TAG_RE = re.compile(r"<next-explore>(.*)</next-explore>", re.DOTALL)
_TASK_PLAN_RE = re.compile(r"<task_plan>\s*(.*?)\s*</task_plan>", re.DOTALL)


# ═══ Parsed output ═══

@dataclass
class ParsedOutput:
    """Result of parsing an LLM response through the tag protocol."""

    reply_text: str | None = None      # Content for the user (extracted or raw fallback)
    explore_hints: str | None = None   # Raw <next-explore> content
    task_plan_json: str | None = None  # Raw JSON from <task_plan>
    raw_text: str = ""                 # Original unmodified response
    tags_present: list[str] = field(default_factory=list)  # Which tags were found


# ═══ Input wrapping ═══

def wrap_user_input(
    text: str,
    sender_name: str = "",
    sender_id: str = "",
    chat_id: str = "",
    chat_type: str = "",
    timestamp: datetime | None = None,
) -> str:
    """Wrap user message in <user-input> tags with metadata.

    Format: <user-input>\\n[2026-03-19 14:30] sender: message\\n</user-input>
    """
    ts = timestamp or datetime.now()
    ts_str = ts.strftime("%Y-%m-%d %H:%M")

    sender_tag = sender_name or ""
    if chat_type == "group" and sender_tag and chat_id:
        sender_tag += f"@{chat_id[:8]}"

    if sender_tag:
        inner = f"[{ts_str}] {sender_tag}: {text}"
    else:
        inner = f"[{ts_str}] {text}"

    # Inject sender open_id so tools (e.g. calendar) can reference the user
    meta = ""
    if sender_id:
        meta = f"\n<sender-context open_id=\"{sender_id}\" />"

    return f"<user-input>\n{inner}\n</user-input>{meta}"


def inject_notifications(prompt: str, notifications: list[str]) -> str:
    """Prepend background notifications block before the user input.

    Notifications are marked as low-priority system context — the model
    should prioritize the user's actual message.
    """
    if not notifications:
        return prompt

    notice_block = (
        "====== 后台通知（已通过飞书独立送达用户）======\n"
        "以下事件发生在上次对话之后。优先回复用户当前消息；\n"
        "仅当通知内容与用户话题相关或需要用户关注时，在回复末尾简要提及。\n"
        + "\n".join(notifications)
        + "\n============\n\n"
    )
    return notice_block + prompt


# ═══ Output parsing ═══

def parse_output(text: str) -> ParsedOutput:
    """Parse LLM response, extracting tagged sections.

    Priority logic:
    1. If <reply-to-user> present → only tagged content sent to user
    2. If absent → full text sent as-is (fallback, logged for monitoring)
    3. <next-explore> always stripped from user-visible output
    4. <task_plan> extracted for orchestrator, stripped from output

    Returns ParsedOutput with all extracted sections.
    """
    result = ParsedOutput(raw_text=text)

    # ── Extract <task_plan> (must happen before reply extraction) ──
    plan_match = _TASK_PLAN_RE.search(text)
    if plan_match:
        result.task_plan_json = plan_match.group(1).strip()
        result.tags_present.append("task_plan")
        # Remove from text so it doesn't leak to user
        text = _TASK_PLAN_RE.sub("", text).strip()

    # ── Extract <next-explore> ──
    explore_match = _EXPLORE_TAG_RE.search(text)
    if explore_match:
        result.explore_hints = explore_match.group(1).strip()
        result.tags_present.append("next-explore")
        # Strip from text (defense in depth — even if reply extraction fails)
        text = _EXPLORE_TAG_RE.sub("", text).strip()

    # ── Extract <reply-to-user> (whitelist filter) ──
    reply_matches = _REPLY_TAG_RE.findall(text)
    if reply_matches:
        result.tags_present.append("reply-to-user")
        filtered = "\n\n".join(t.strip() for t in reply_matches if t.strip())
        result.reply_text = filtered if filtered else None
    else:
        # No tags — send full (cleaned) text as-is
        result.reply_text = text if text.strip() else None

    return result
