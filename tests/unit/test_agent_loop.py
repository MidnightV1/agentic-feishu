"""Agent loop tests — tool calling cycle, budget control, error handling.

Covers:
- Single turn (no tools) → end_turn
- Multi-turn tool calling loop
- Parallel tool execution
- max_turns / max_budget exit conditions
- Tool error reporting to LLM
- P0-4: tool message name field correctness
"""
import pytest
from unittest.mock import AsyncMock

from core.types import Message, ToolCall, Usage, RunConfig

# All tests use stream=False for deterministic mock behavior.
# Streaming tests would need async iterator mocks.
CFG = RunConfig(stream=False)
CFG3 = RunConfig(stream=False, max_turns=3)
CFG_CHEAP = RunConfig(stream=False, max_budget_usd=0.001)


# ── Helpers ──────────────────────────────────────────────

def make_registry_with(*tools):
    from core.tool_registry import ToolRegistry
    reg = ToolRegistry()
    for t in tools:
        reg.register_decorated(t)
    return reg


def make_empty_registry():
    from core.tool_registry import ToolRegistry
    return ToolRegistry()


def mock_chat_sequence(provider, *messages):
    """Set provider.chat to return Messages in sequence."""
    it = iter(messages)
    provider.chat = AsyncMock(side_effect=lambda *a, **kw: next(it))


# ── Single turn ──────────────────────────────────────────

class TestSingleTurn:

    async def test_no_tools_returns_end_turn(self, mock_provider):
        from core.agent_loop import AgentLoop

        loop = AgentLoop(provider=mock_provider, tool_registry=make_empty_registry())
        result = await loop.run(prompt="hello", config=CFG)
        assert result.stop_reason == "end_turn"
        assert result.turn_count == 1

    async def test_assistant_no_tool_calls_stops(self, mock_provider):
        mock_provider.chat = AsyncMock(
            return_value=Message(role="assistant", content="answer"))
        from core.agent_loop import AgentLoop

        loop = AgentLoop(provider=mock_provider, tool_registry=make_empty_registry())
        result = await loop.run(prompt="question", config=CFG)
        assert result.stop_reason == "end_turn"


# ── Tool calling loop ────────────────────────────────────

class TestToolCallingLoop:

    async def test_tool_call_then_response(self, mock_provider, echo_tool):
        from core.agent_loop import AgentLoop

        mock_chat_sequence(mock_provider,
            Message(role="assistant", content="", tool_calls=[
                ToolCall(id="tc_1", name="echo", arguments={"text": "hello"}),
            ]),
            Message(role="assistant", content="Done: hello"),
        )
        loop = AgentLoop(provider=mock_provider, tool_registry=make_registry_with(echo_tool))
        result = await loop.run(prompt="echo hello", config=CFG)

        assert result.turn_count == 2
        assert "Done" in result.text
        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == "hello"

    async def test_tool_message_name_is_function_name(self, mock_provider, echo_tool):
        """P0-4: tool result name must be function name, not call ID."""
        from core.agent_loop import AgentLoop

        mock_chat_sequence(mock_provider,
            Message(role="assistant", content="", tool_calls=[
                ToolCall(id="tc_abc123", name="echo", arguments={"text": "test"}),
            ]),
            Message(role="assistant", content="done"),
        )
        loop = AgentLoop(provider=mock_provider, tool_registry=make_registry_with(echo_tool))
        result = await loop.run(prompt="echo test", config=CFG)

        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert tool_msgs[0].name == "echo", \
            f"expected 'echo', got '{tool_msgs[0].name}'"
        assert tool_msgs[0].name != "tc_abc123"


# ── Parallel tools ───────────────────────────────────────

class TestParallelTools:

    async def test_parallel_safe_tools_concurrent(self, mock_provider, echo_tool):
        from core.agent_loop import AgentLoop

        mock_chat_sequence(mock_provider,
            Message(role="assistant", tool_calls=[
                ToolCall(id="tc_1", name="echo", arguments={"text": "a"}),
                ToolCall(id="tc_2", name="echo", arguments={"text": "b"}),
            ]),
            Message(role="assistant", content="done"),
        )
        loop = AgentLoop(provider=mock_provider, tool_registry=make_registry_with(echo_tool))
        result = await loop.run(prompt="echo a and b", config=CFG)

        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) == 2
        assert {m.content for m in tool_msgs} == {"a", "b"}


# ── Exit conditions ──────────────────────────────────────

class TestExitConditions:

    async def test_max_turns_exit(self, mock_provider, echo_tool):
        from core.agent_loop import AgentLoop

        mock_provider.chat = AsyncMock(return_value=Message(
            role="assistant", tool_calls=[
                ToolCall(id="tc", name="echo", arguments={"text": "loop"}),
            ],
        ))
        loop = AgentLoop(provider=mock_provider, tool_registry=make_registry_with(echo_tool))
        result = await loop.run(prompt="loop", config=CFG3)
        assert result.stop_reason == "max_turns"
        assert result.turn_count == 3

    async def test_max_budget_exit(self, mock_provider):
        from core.agent_loop import AgentLoop

        mock_provider.chat = AsyncMock(return_value=Message(
            role="assistant", content="expensive",
        ))
        mock_provider._last_usage = Usage(input_tokens=500000, output_tokens=200000)

        loop = AgentLoop(provider=mock_provider, tool_registry=make_empty_registry())
        result = await loop.run(prompt="test", config=CFG_CHEAP)
        assert result.stop_reason == "max_budget"


# ── Error handling ───────────────────────────────────────

class TestErrorHandling:

    async def test_tool_error_reported_as_tool_result(self, mock_provider, failing_tool):
        from core.agent_loop import AgentLoop

        mock_chat_sequence(mock_provider,
            Message(role="assistant", tool_calls=[
                ToolCall(id="tc_1", name="fail_tool", arguments={}),
            ]),
            Message(role="assistant", content="The tool failed."),
        )
        loop = AgentLoop(provider=mock_provider, tool_registry=make_registry_with(failing_tool))
        result = await loop.run(prompt="test", config=CFG)

        tool_msgs = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert "Error" in tool_msgs[0].content or "error" in tool_msgs[0].content
