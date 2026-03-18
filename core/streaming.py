"""Streaming support — accumulates deltas into complete messages."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .types import AsyncCallback, ContentBlock, Message, ToolCall, Usage

log = logging.getLogger(__name__)


@dataclass
class _ToolCallAccumulator:
    """Buffers partial tool_call argument chunks."""

    id: str = ""
    name: str = ""
    arguments_buf: str = ""

    def to_tool_call(self) -> ToolCall:
        try:
            args = json.loads(self.arguments_buf) if self.arguments_buf else {}
        except json.JSONDecodeError:
            log.warning("malformed tool_call arguments for %s: %s", self.name, self.arguments_buf[:200])
            args = {}
        return ToolCall(id=self.id, name=self.name, arguments=args)


@dataclass
class StreamAccumulator:
    """Accumulates streaming chunks into a complete assistant Message.

    Feed chunks via `add_*` methods. When the stream ends, call `finish()`
    to get the assembled Message and Usage.
    """

    _text_buf: str = ""
    _tool_calls: dict[int, _ToolCallAccumulator] = field(default_factory=dict)
    _usage: Usage = field(default_factory=Usage)
    _on_text: AsyncCallback | None = None

    @classmethod
    def create(cls, on_text: AsyncCallback | None = None) -> StreamAccumulator:
        return cls(_on_text=on_text)

    async def add_text(self, delta: str) -> None:
        """Append a text delta and fire the text callback."""
        self._text_buf += delta
        if self._on_text:
            await self._on_text(delta)

    def add_tool_call_start(self, index: int, tool_call_id: str, name: str) -> None:
        """Begin accumulating a new tool_call at the given index."""
        self._tool_calls[index] = _ToolCallAccumulator(id=tool_call_id, name=name)

    def add_tool_call_delta(self, index: int, arguments_delta: str) -> None:
        """Append a partial JSON arguments chunk."""
        acc = self._tool_calls.get(index)
        if acc is None:
            log.warning("tool_call delta for unknown index %d", index)
            return
        acc.arguments_buf += arguments_delta

    def set_usage(self, usage: Usage) -> None:
        self._usage = usage

    def finish(self) -> tuple[Message, Usage]:
        """Assemble the final Message and return (message, usage)."""
        tool_calls = None
        if self._tool_calls:
            tool_calls = [
                self._tool_calls[i].to_tool_call()
                for i in sorted(self._tool_calls)
            ]

        content: str | list[ContentBlock] | None
        if self._text_buf and tool_calls:
            # Both text and tool_calls — use content blocks
            blocks = [ContentBlock(type="text", text=self._text_buf)]
            for tc in tool_calls:
                blocks.append(ContentBlock(type="tool_use", id=tc.id, name=tc.name, input=tc.arguments))
            content = blocks
        elif self._text_buf:
            content = self._text_buf
        else:
            content = None

        msg = Message(role="assistant", content=content, tool_calls=tool_calls)
        return msg, self._usage
