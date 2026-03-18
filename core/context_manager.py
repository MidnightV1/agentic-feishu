"""Context window management: compression, token tracking, system prompt assembly."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .types import Message

if TYPE_CHECKING:
    from providers.base import BaseProvider

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

### 当前状态
- 已完成的任务
- 进行中的工作
- 待确认的事项

### 用户偏好
- 用户明确表达的偏好或纠正

## 要求
- 保留具体文件路径、命令、配置值
- 丢弃寒暄和已否决方案的细节
- 优先保留最近的信息
"""


@dataclass
class ContextConfig:
    """Tuning knobs for context management."""

    compress_threshold: float = 0.8  # compress at 80% of window
    compress_strategy: str = "hybrid"  # "summary" | "sliding_window" | "hybrid"
    recent_rounds_keep: int = 5  # keep last N rounds raw in hybrid mode
    max_summary_tokens: int = 4000  # max tokens for summary
    pin_system_messages: bool = True  # never compress system messages


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

    def __init__(self, provider: BaseProvider, config: ContextConfig | None = None):
        self.provider = provider
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

    async def count_tokens(self, messages: list[Message]) -> int:
        """Count tokens for the given messages using the provider."""
        return await self.provider.count_tokens(messages)

    async def should_compress(
        self, messages: list[Message], context_window: int
    ) -> bool:
        """Return True if token count exceeds *threshold* fraction of *context_window*."""
        tokens = await self.count_tokens(messages)
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

        old = compressible[: -keep_n * 2]
        recent = compressible[-keep_n * 2 :]

        try:
            summarized = await self._summarize(old)
        except Exception:
            logger.warning("Summary compression failed, falling back to sliding_window")
            summarized = []

        return pinned + summarized + recent

    # -- Internals ----------------------------------------------------------

    async def _summarize(self, messages: list[Message]) -> list[Message]:
        """Ask the provider to compress *messages* into a summary message."""
        if not messages:
            return []

        # Build a transcript for the LLM
        transcript_parts: list[str] = []
        for m in messages:
            transcript_parts.append(f"[{m.role}] {m.text}")
        transcript = "\n".join(transcript_parts)

        prompt_msg = Message(
            role="user",
            content=f"{SUMMARY_PROMPT}\n\n---\n\n{transcript}",
        )

        try:
            response = await self.provider.chat(
                messages=[prompt_msg],
                stream=False,
                max_tokens=self.config.max_summary_tokens,
            )
            summary_text = response.text if hasattr(response, "text") else str(response.content)
        except Exception:
            logger.exception("LLM summarization failed")
            raise

        return [
            Message(
                role="system",
                content=f"[compressed history]\n{summary_text}",
            )
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _section_header(component_type: str) -> str:
    """Map component type to a markdown header."""
    headers = {
        "base_instructions": "# Instructions",
        "skill_descriptions": "# Available Skills",
        "environment": "# Environment",
        "persona": "# Persona",
        "session_context": "# Session Context",
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
    return messages[cut:]
