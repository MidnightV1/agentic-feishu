# -*- coding: utf-8 -*-
"""Smoke test — end-to-end agent loop with a mock provider and tool.

Validates: message → agent loop → tool call → tool result → final reply.
No real LLM or Feishu connection needed.
"""

from __future__ import annotations

import asyncio
import pytest

from core.agent_loop import AgentLoop
from core.types import Callbacks, Message, RunConfig, RunResult, ToolCall, ToolResult, Usage
from core.tool_registry import ToolRegistry, tool


# ── Mock provider ──────────────────────────────────────────────


class MockProvider:
    """Simulates LLM responses: first call returns a tool call, second returns text."""

    def __init__(self):
        self.call_count = 0

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = False,
        **kwargs,
    ) -> Message | AsyncMockStream:
        self.call_count += 1

        if stream:
            return self._stream_response()

        if self.call_count == 1 and tools:
            # First turn: request a tool call
            tc = ToolCall(id="tc_1", name="echo", arguments={"text": "hello from tool"})
            msg = Message(role="assistant", content="Let me call echo.", tool_calls=[tc])
            self._last_usage = Usage(input_tokens=10, output_tokens=5)
            return msg
        else:
            # Second turn: final text response
            msg = Message(role="assistant", content="Tool returned: hello from tool")
            self._last_usage = Usage(input_tokens=15, output_tokens=8)
            return msg

    async def _stream_response(self):
        """Async generator that yields text deltas then sets _last_message."""
        yield "Tool "
        yield "returned: "
        yield "hello"
        self._last_message = Message(role="assistant", content="Tool returned: hello")
        self._last_usage = Usage(input_tokens=10, output_tokens=5)


class AsyncMockStream:
    pass


# ── Fixtures ──────────────────────────────────────────────────


@tool(description="Echo the input text", parallel_safe=True)
async def echo(text: str) -> str:
    """Echo tool for testing.

    Args:
        text: The text to echo back.
    """
    return f"echoed: {text}"


@pytest.fixture
def registry():
    reg = ToolRegistry()
    reg.register_decorated(echo)
    return reg


@pytest.fixture
def agent(registry):
    provider = MockProvider()
    return AgentLoop(provider=provider, tool_registry=registry)


# ── Tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_smoke_agent_loop(agent):
    """Full cycle: user prompt → tool call → tool result → final text."""
    config = RunConfig(model="mock", provider="mock", max_turns=5, stream=False)

    result = await agent.run(
        prompt="Say hello using the echo tool",
        config=config,
    )

    assert result.text, "Expected non-empty final response"
    assert result.turn_count >= 2, "Expected at least 2 turns (tool call + final)"
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0


@pytest.mark.asyncio
async def test_smoke_with_system_prompt(agent):
    """Agent loop with system prompt prepended."""
    config = RunConfig(model="mock", provider="mock", max_turns=5, stream=False)

    result = await agent.run(
        prompt="test",
        config=config,
        system_prompt="You are a test assistant.",
    )

    assert result.text


@pytest.mark.asyncio
async def test_tool_registry_schema(registry):
    """Tool registry generates valid JSON schemas."""
    schemas = registry.get_tool_schemas()
    assert len(schemas) >= 1
    echo_schema = next(s for s in schemas if s["function"]["name"] == "echo")
    assert "text" in echo_schema["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_tool_execution(registry):
    """Tool execution returns correct result."""
    result = await registry.execute("echo", {"text": "world"})
    assert isinstance(result, ToolResult)
    assert "echoed: world" in result.content
    assert not result.is_error
