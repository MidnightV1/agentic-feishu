"""Shared types for the agent loop."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Coroutine

# Type alias for async callbacks
AsyncCallback = Callable[..., Coroutine[Any, Any, None]]


@dataclass
class ContentBlock:
    """A single content block within a message."""

    type: str  # "text" | "tool_use" | "tool_result"
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Result from executing a tool."""

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    """A conversation message."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | list[ContentBlock] | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    reasoning_content: str | None = None  # DeepSeek Reasoner thinking output

    @property
    def text(self) -> str:
        """Extract plain text from content."""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            parts = [b.text for b in self.content if b.type == "text" and b.text]
            return "\n".join(parts)
        return ""


@dataclass
class Usage:
    """Token usage statistics."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    def __iadd__(self, other: Usage) -> Usage:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        return self


@dataclass
class RunConfig:
    """Configuration for an agent loop run."""

    model: str = ""
    provider: str = ""
    max_turns: int = 50
    max_budget_usd: float = 1.0
    temperature: float = 0.7
    stream: bool = True


@dataclass
class RunResult:
    """Result of a completed agent loop run."""

    text: str = ""
    messages: list[Message] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    turn_count: int = 0
    stop_reason: str = ""  # "end_turn" | "max_turns" | "max_budget"


@dataclass
class Callbacks:
    """Async callbacks for observing agent loop progress."""

    on_text: AsyncCallback | None = None
    on_tool_start: AsyncCallback | None = None
    on_tool_end: AsyncCallback | None = None
    on_turn_end: AsyncCallback | None = None
