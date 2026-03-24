"""Context window management: compression, token tracking, system prompt assembly."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .types import Message

if TYPE_CHECKING:
    from providers.base import BaseProvider
    from infra.session import SessionStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

SUMMARY_PROMPT = """将以下对话历史压缩为结构化摘要。

## 输出格式

### 对话主题
一句话概括

### 关键决策与结果
- 决策内容（保留文件路径、配置值、技术选型）
- 决策理由

### 涉及的文件与变更
- 文件路径 → 做了什么变更
- 创建的文档/任务（保留 ID 和链接）

### 当前状态
- 已完成的任务
- 进行中的工作
- 待确认的事项

### 用户偏好与纠正
- 用户明确表达的偏好
- 用户对你行为的纠正（这些尤其重要，必须保留）

### 上下文要点
- 用户提到的背景信息
- 尚未解决的问题

## 要求
- 保留具体文件路径、命令、配置值、文档 ID
- 丢弃寒暄、重复、已否决方案的细节（仅记录「排除了 X，因为 Y」）
- 代码修改只记录改了哪些文件的什么方面，不记录代码本身
- 优先保留最近的信息
- 用户纠正的权重最高，必须完整保留
"""


RECOVERY_PREAMBLE = (
    "## 会话恢复\n\n"
    "你的上一个会话已结束，以下是之前对话的记录。\n"
    "注意：之前的工具调用记录（文件读写、命令执行）不可访问，"
    "如需读取文件请重新操作。"
)

RECOVERY_RECENT_ROUNDS = 15   # max messages to include in recovery
RECOVERY_TRUNCATE = 4000      # max chars per message


@dataclass
class ContextConfig:
    """Tuning knobs for context management."""

    compress_threshold: float = 0.8  # compress at 80% of window
    compress_strategy: str = "hybrid"  # "summary" | "sliding_window" | "hybrid"
    recent_rounds_keep: int = 5  # keep last N rounds raw in hybrid mode
    max_summary_tokens: int = 4000  # max tokens for summary
    pin_system_messages: bool = True  # never compress system messages
    compress_timeout: int = 60  # seconds; 0 = no timeout


@dataclass
class ContextComponent:
    """A section of the system prompt."""

    type: str  # "base_instructions" | "skill_descriptions" | "environment" | "persona" | "session_context"
    content: str
    priority: int = 0  # higher = more important, preserved during compression


# ---------------------------------------------------------------------------
# ContextManager
# ---------------------------------------------------------------------------


class ContextManager:
    """Manages context window, compression, and system prompt assembly."""

    def __init__(
        self,
        provider: BaseProvider,
        config: ContextConfig | None = None,
        fallback_provider: BaseProvider | None = None,
    ):
        self.provider = provider
        self.fallback_provider = fallback_provider
        self.config = config or ContextConfig()

    # -- System prompt assembly ---------------------------------------------

    async def build_system_prompt(self, components: list[ContextComponent]) -> str:
        """Assemble system prompt from ordered components.

        Components are injected in priority order (higher first within same
        position).  Tool definitions are handled by agent_loop and excluded
        here.
        """
        ordered = sorted(components, key=lambda c: -c.priority)
        sections: list[str] = []
        for comp in ordered:
            if not comp.content:
                continue
            header = _section_header(comp.type)
            sections.append(f"{header}\n{comp.content}")
        return "\n\n".join(sections)

    # -- Token tracking & decision ------------------------------------------

    async def count_tokens(
        self, messages: list[Message], tools: list[dict] | None = None,
    ) -> int:
        """Count tokens for the given messages using the provider.

        If *tools* is provided, estimate the token overhead of tool/function
        definitions and add it to the message token count.  Estimation uses
        len(json.dumps(tools)) // 4 (≈ 1 token per 4 chars) which is a
        reasonable approximation for both tiktoken and provider-native counts.
        """
        msg_tokens = await self.provider.count_tokens(messages)
        if tools:
            tools_text = json.dumps(tools, ensure_ascii=False)
            msg_tokens += len(tools_text) // 4
        return msg_tokens

    async def should_compress(
        self, messages: list[Message], context_window: int,
        tools: list[dict] | None = None,
    ) -> bool:
        """Return True if token count exceeds *threshold* fraction of *context_window*."""
        tokens = await self.count_tokens(messages, tools=tools)
        limit = int(context_window * self.config.compress_threshold)
        over = tokens > limit
        if over:
            logger.info(
                "Compression triggered: %d tokens > %d limit (%.0f%% of %d)",
                tokens, limit, self.config.compress_threshold * 100, context_window,
            )
        return over

    # -- Compression --------------------------------------------------------

    async def compress(
        self, messages: list[Message], strategy: str | None = None
    ) -> list[Message]:
        """Compress conversation history, preserving recent messages and pins.

        Falls back to sliding_window if LLM summarization fails.
        """
        strategy = strategy or self.config.compress_strategy
        keep_n = self.config.recent_rounds_keep

        # Separate pinned messages (system, or explicitly pinned)
        pinned, compressible = _split_pinned(messages, self.config.pin_system_messages)

        if strategy == "sliding_window":
            return pinned + _keep_recent(compressible, keep_n)

        if strategy == "summary":
            return pinned + await self._summarize(compressible)

        # hybrid (default): summarize old + keep recent raw
        if len(compressible) <= keep_n * 2:
            # not enough to split — keep as-is
            return messages

        split_idx = len(compressible) - keep_n * 2
        split_idx = _adjust_split_for_tool_groups(compressible, split_idx)
        old = compressible[:split_idx]
        recent = compressible[split_idx:]

        try:
            summarized = await self._summarize(old)
        except Exception:
            logger.warning("Summary compression failed, falling back to sliding_window")
            summarized = []

        return pinned + summarized + recent

    # -- Internals ----------------------------------------------------------

    async def _summarize(self, messages: list[Message]) -> list[Message]:
        """Ask the provider to compress *messages* into a summary message.

        Strategy: primary provider with timeout -> fallback provider -> raise.
        """
        if not messages:
            return []

        transcript_parts: list[str] = []
        for m in messages:
            transcript_parts.append(f"[{m.role}] {m.text}")
        transcript = "\n".join(transcript_parts)

        prompt_msg = Message(
            role="user",
            content=f"{SUMMARY_PROMPT}\n\n---\n\n{transcript}",
        )

        timeout = self.config.compress_timeout or None

        # Primary provider
        summary_text = await self._try_compress(self.provider, prompt_msg, timeout, "primary")

        # Fallback provider
        if summary_text is None and self.fallback_provider:
            fallback_timeout = max(30, (timeout or 60) // 2)
            summary_text = await self._try_compress(
                self.fallback_provider, prompt_msg, fallback_timeout, "fallback",
            )

        if summary_text is None:
            raise RuntimeError("All compression providers failed")

        return [
            Message(
                role="system",
                content=f"[compressed history]\n{summary_text}",
            )
        ]

    async def _try_compress(
        self,
        provider: BaseProvider,
        prompt_msg: Message,
        timeout: int | None,
        label: str,
    ) -> str | None:
        """Attempt compression with a single provider. Returns summary text or None."""
        try:
            coro = provider.chat(
                messages=[prompt_msg],
                stream=False,
                max_tokens=self.config.max_summary_tokens,
            )
            if timeout:
                response = await asyncio.wait_for(coro, timeout=timeout)
            else:
                response = await coro
            text = response.text if hasattr(response, "text") else str(response.content)
            if text and text.strip():
                logger.info(
                    "Compression via %s (%s) succeeded: %d chars",
                    label, provider.name, len(text),
                )
                return text.strip()
            logger.warning("Compression via %s returned empty result", label)
        except asyncio.TimeoutError:
            logger.warning("Compression via %s timed out after %ds", label, timeout)
        except Exception:
            logger.exception("Compression via %s failed", label)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section_header(component_type: str) -> str:
    """Map component type to a markdown header."""
    headers = {
        "soul": "# Soul",
        "agent": "# Agent",
        "base_instructions": "# Instructions",
        "platform_rules": "# Platform",
        "tool_guidelines": "# Tool Guidelines",
        "skill_descriptions": "# Available Skills",
        "environment": "# Environment",
        "session_context": "# Session Context",
        # --- context_assembler types ---
        "org_soul": "# 组织灵魂",
        "org_cognition": "# 组织认知",
        "bot_soul": "# Bot 灵魂",
        "bot_rules": "# 行为规则",
        "persona": "# 角色设定",
        "tools": "# 可用工具",
        "shared_knowledge": "# 共享知识",
        "corrections": "# 用户纠正",
        "user_profile": "# 用户画像",
        "user_memory": "# 用户记忆",
        "platform": "# 平台协议",
        "recovery": "",  # RECOVERY_PREAMBLE already has its own header
    }
    return headers.get(component_type, f"# {component_type}")


def _split_pinned(
    messages: list[Message], pin_system: bool
) -> tuple[list[Message], list[Message]]:
    """Partition messages into pinned (never compressed) and compressible."""
    pinned: list[Message] = []
    rest: list[Message] = []
    for m in messages:
        is_pinned = getattr(m, "_pin", False) or (pin_system and m.role == "system")
        if is_pinned:
            pinned.append(m)
        else:
            rest.append(m)
    return pinned, rest



def _adjust_split_for_tool_groups(messages: list[Message], cut: int) -> int:
    """Adjust a split index so tool-call atomic groups are not broken.

    An assistant message with tool_calls and its subsequent tool-result
    messages form an atomic group.  If *cut* falls inside such a group
    (after the assistant but before the last tool result), move *cut*
    back to before the assistant so the whole group stays on the recent
    side.
    """
    if cut <= 0 or cut >= len(messages):
        return cut
    # Walk backwards from cut to see if we are inside an atomic group
    for i in range(cut - 1, -1, -1):
        m = messages[i]
        if m.role == "tool":
            continue  # still inside tool results
        if m.role == "assistant" and getattr(m, "tool_calls", None):
            # cut is between this assistant(tool_calls) and its results
            return i
        break  # hit a non-tool, non-assistant-with-calls -- no adjustment
    return cut


def _keep_recent(messages: list[Message], rounds: int) -> list[Message]:
    """Keep the last *rounds* user-assistant pairs (+ any trailing)."""
    if rounds <= 0 or not messages:
        return messages
    # Walk backwards counting user messages as round boundaries
    count = 0
    cut = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].role == "user":
            count += 1
            if count >= rounds:
                cut = i
                break
    cut = _adjust_split_for_tool_groups(messages, cut)
    return messages[cut:]


# ---------------------------------------------------------------------------
# Context recovery for session loss
# ---------------------------------------------------------------------------


async def build_recovery_context(
    session_store: SessionStore,
    session_key: str,
    limit: int = RECOVERY_RECENT_ROUNDS,
    truncate: int = RECOVERY_TRUNCATE,
) -> ContextComponent | None:
    """Build a recovery context component from stored conversation history.

    Called when a new session starts for a chat that already has history
    (restart, crash, timeout).  Returns a ``ContextComponent`` of type
    ``"recovery"`` suitable for ``ContextManager.build_system_prompt``,
    or ``None`` if there is no prior history to recover.

    Enhanced formatting:
    - assistant(tool_calls) messages show which tools were called
    - tool result messages show tool name + truncated output (first 500 chars)
    - plain user/assistant/system messages show content as before
    """
    messages = await session_store.get_recent_messages(
        session_key, limit=limit, truncate=truncate,
    )
    if not messages:
        return None

    # Format as readable transcript with tool call awareness
    lines: list[str] = [RECOVERY_PREAMBLE, ""]
    role_label = {"user": "用户", "assistant": "助手", "system": "系统", "tool": "工具"}
    for m in messages:
        role = m["role"]
        label = role_label.get(role, role)
        tool_calls = m.get("tool_calls")
        tool_name = m.get("name")

        if role == "assistant" and tool_calls:
            # Assistant message with tool invocations — extract tool names
            names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
            text_part = m.get("content", "") or ""
            if text_part.strip():
                lines.append(f"**助手**: {text_part.strip()}")
            lines.append(f"**助手**: [调用了 {', '.join(names)}]")
        elif role == "tool":
            # Tool result — show name and truncated content for key conclusions
            content = m.get("content", "") or ""
            display_name = tool_name or "unknown"
            truncated = content[:500] + ("..." if len(content) > 500 else "")
            lines.append(f"**工具 {display_name}**: {truncated}")
        else:
            content = m.get("content", "") or ""
            lines.append(f"**{label}**: {content}")
    transcript = "\n\n".join(lines)

    logger.info(
        "Built recovery context for %s: %d messages, %d chars",
        session_key, len(messages), len(transcript),
    )

    return ContextComponent(
        type="recovery",
        content=transcript,
        priority=-10,  # low priority — placed after all other components
    )
