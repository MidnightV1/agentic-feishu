"""Context manager tests — compression, token counting, tool pair integrity.

Covers:
- P0-5: compression must not split tool_call/tool_result pairs
- CTX-2: token counting must include tool definitions
- Compression trigger threshold
- Hybrid strategy: recent rounds preserved, older compressed
- Fallback on compression failure
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.types import Message, ToolCall
from core.context_manager import (
    ContextManager, ContextConfig, ContextComponent,
    _adjust_split_for_tool_groups, _keep_recent, _split_pinned,
)


# ── Helpers ──────────────────────────────────────────────

def _make_provider():
    """Create a minimal mock provider for ContextManager."""
    p = AsyncMock()
    p.count_tokens = AsyncMock(return_value=1000)
    return p


def _make_mgr(**config_kw) -> ContextManager:
    """Shorthand: create ContextManager with mock provider and custom config."""
    return ContextManager(provider=_make_provider(), config=ContextConfig(**config_kw))


def make_conversation(rounds: int) -> list[Message]:
    """Generate a conversation with N rounds (user + assistant each)."""
    messages = []
    for i in range(rounds):
        messages.append(Message(role="user", content=f"Question {i}"))
        messages.append(Message(role="assistant", content=f"Answer {i}"))
    return messages


def make_conversation_with_tools(rounds: int) -> list[Message]:
    """Generate conversation where every other round has tool calls."""
    messages = []
    for i in range(rounds):
        messages.append(Message(role="user", content=f"Request {i}"))
        if i % 2 == 0:
            messages.append(Message(
                role="assistant", content="",
                tool_calls=[ToolCall(id=f"tc_{i}", name="read_file",
                                     arguments={"path": f"file{i}.py"})],
            ))
            messages.append(Message(
                role="tool", content=f"content of file{i}.py",
                tool_call_id=f"tc_{i}", name="read_file",
            ))
            messages.append(Message(
                role="assistant", content=f"File {i} contains...",
            ))
        else:
            messages.append(Message(
                role="assistant", content=f"Simple answer {i}",
            ))
    return messages


# ── Compression trigger ──────────────────────────────────

class TestCompressionTrigger:

    async def test_compress_above_threshold(self):
        """Token count > context_window * threshold → should compress."""
        mgr = _make_mgr(context_window=40000, compress_threshold=0.8)
        mgr.provider.count_tokens = AsyncMock(return_value=33000)  # > 32000

        messages = make_conversation(20)
        assert await mgr.should_compress(messages, context_window=40000) is True

    async def test_no_compress_below_threshold(self):
        mgr = _make_mgr(context_window=40000, compress_threshold=0.8)
        mgr.provider.count_tokens = AsyncMock(return_value=20000)  # < 32000

        messages = make_conversation(5)
        assert await mgr.should_compress(messages, context_window=40000) is False


# ── Tool call/result pair integrity (P0-5) ───────────────

class TestToolPairIntegrity:
    """P0-5: _adjust_split_for_tool_groups must never split tool-call atomic groups."""

    def test_split_keeps_pairs_together(self):
        """Cut inside a tool group → adjust to keep group intact."""
        messages = [
            Message(role="user", content="Q1"),
            Message(role="assistant", content="", tool_calls=[
                ToolCall(id="tc_1", name="bash", arguments={"cmd": "ls"}),
            ]),
            Message(role="tool", content="file.py", tool_call_id="tc_1", name="bash"),
            Message(role="assistant", content="Found file.py"),
            Message(role="user", content="Q2"),
            Message(role="assistant", content="A2"),
        ]

        # Cut at index 2 (middle of tool group) → should adjust
        adjusted = _adjust_split_for_tool_groups(messages, cut=2)
        # The adjusted cut must not split the tool group
        old = messages[:adjusted]
        recent = messages[adjusted:]

        # Verify: in 'old', every assistant with tool_calls has its tool results
        for i, msg in enumerate(old):
            if msg.role == "assistant" and msg.tool_calls:
                expected_ids = {tc.id for tc in msg.tool_calls}
                j = i + 1
                found_ids = set()
                while j < len(old) and old[j].role == "tool":
                    found_ids.add(old[j].tool_call_id)
                    j += 1
                assert expected_ids == found_ids, \
                    f"Tool pair broken in 'old': expected {expected_ids}, found {found_ids}"

    def test_multiple_tool_calls_in_one_turn(self):
        """Assistant with 2 tool_calls → both results must stay together."""
        messages = [
            Message(role="user", content="Read both files"),
            Message(role="assistant", content="", tool_calls=[
                ToolCall(id="tc_a", name="read_file", arguments={"path": "a.py"}),
                ToolCall(id="tc_b", name="read_file", arguments={"path": "b.py"}),
            ]),
            Message(role="tool", content="aaa", tool_call_id="tc_a", name="read_file"),
            Message(role="tool", content="bbb", tool_call_id="tc_b", name="read_file"),
            Message(role="assistant", content="Both files read."),
            Message(role="user", content="Thanks"),
            Message(role="assistant", content="You're welcome."),
        ]

        # Cut at index 3 (between the two tool results) → should adjust
        adjusted = _adjust_split_for_tool_groups(messages, cut=3)
        old = messages[:adjusted]

        # The 4-message tool block (assistant+2tools+assistant) must be intact
        tc_msg = [m for m in old if m.role == "assistant" and m.tool_calls]
        if tc_msg:
            idx = old.index(tc_msg[0])
            assert old[idx + 1].role == "tool"
            assert old[idx + 2].role == "tool"


# ── Hybrid strategy ──────────────────────────────────────

class TestHybridStrategy:

    def test_keep_recent_rounds(self):
        """_keep_recent preserves last N rounds."""
        messages = make_conversation(20)
        recent = _keep_recent(messages, rounds=5)
        # Last 5 rounds = 10 messages
        assert len(recent) == 10
        assert recent[0].content == "Question 15"

    async def test_compression_fallback(self):
        """Hybrid: summary failure → fallback to sliding_window (keep recent)."""
        mgr = _make_mgr(context_window=40000, recent_rounds_keep=5)
        mgr._summarize = AsyncMock(side_effect=Exception("LLM timeout"))

        messages = make_conversation(20)
        # hybrid (default) catches _summarize failure and falls back
        result = await mgr.compress(messages, strategy="hybrid")
        assert result is not None
        # Should at least contain the recent rounds
        contents = [m.content for m in result]
        assert "Question 19" in contents


# ── Token counting (CTX-2) ───────────────────────────────

class TestTokenCounting:

    async def test_count_includes_tools(self):
        """CTX-2: token count must include tool schema tokens."""
        mgr = _make_mgr(context_window=40000)
        # Make count_tokens return different values based on tools presence
        call_count = 0
        async def mock_count(msgs, **kw):
            nonlocal call_count
            call_count += 1
            return 5000 if kw.get("tools") else 1000
        mgr.provider.count_tokens = mock_count

        messages = [Message(role="user", content="hello")]
        tools = [{"type": "function", "function": {
            "name": "bash",
            "description": "Run a command" * 100,
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string", "description": "The command to run"},
            }},
        }}]

        count_with = await mgr.count_tokens(messages, tools=tools)
        count_without = await mgr.count_tokens(messages, tools=None)
        assert count_with > count_without, \
            "Token count with tools must be higher than without"


# ── _split_pinned ─────────────────────────────────────────

class TestSplitPinned:
    def test_system_messages_pinned(self):
        messages = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Hi"),
            Message(role="assistant", content="Hello"),
        ]
        pinned, rest = _split_pinned(messages, pin_system=True)
        assert len(pinned) == 1
        assert pinned[0].role == "system"
        assert len(rest) == 2

    def test_system_not_pinned_when_disabled(self):
        messages = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Hi"),
        ]
        pinned, rest = _split_pinned(messages, pin_system=False)
        assert len(pinned) == 0
        assert len(rest) == 2


# ── _keep_recent with tool groups ─────────────────────────

class TestKeepRecentWithTools:
    def test_preserves_tool_groups(self):
        """_keep_recent should not split tool-call groups."""
        messages = [
            Message(role="user", content="Old question"),
            Message(role="assistant", content="Old answer"),
            Message(role="user", content="Read file"),
            Message(role="assistant", content="", tool_calls=[
                ToolCall(id="tc_1", name="read", arguments={}),
            ]),
            Message(role="tool", content="file content", tool_call_id="tc_1", name="read"),
            Message(role="assistant", content="Here is the content"),
            Message(role="user", content="Thanks"),
            Message(role="assistant", content="Welcome"),
        ]
        recent = _keep_recent(messages, rounds=2)
        # Should include the tool group (user+assistant+tool+assistant)
        roles = [m.role for m in recent]
        # Verify no orphaned tool results
        for i, m in enumerate(recent):
            if m.role == "tool":
                assert i > 0 and recent[i - 1].role == "assistant"

    def test_zero_rounds(self):
        messages = make_conversation(5)
        assert _keep_recent(messages, rounds=0) == messages

    def test_empty_messages(self):
        assert _keep_recent([], rounds=5) == []


# ── build_system_prompt ────────────────────────────────────

class TestBuildSystemPrompt:
    async def test_priority_ordering(self):
        mgr = _make_mgr()
        components = [
            ContextComponent(type="environment", content="env info", priority=10),
            ContextComponent(type="soul", content="soul text", priority=100),
            ContextComponent(type="persona", content="role text", priority=50),
        ]
        prompt = await mgr.build_system_prompt(components)
        # Higher priority should come first
        soul_pos = prompt.find("soul text")
        persona_pos = prompt.find("role text")
        env_pos = prompt.find("env info")
        assert soul_pos < persona_pos < env_pos

    async def test_empty_components_skipped(self):
        mgr = _make_mgr()
        components = [
            ContextComponent(type="soul", content="content", priority=100),
            ContextComponent(type="empty", content="", priority=50),
        ]
        prompt = await mgr.build_system_prompt(components)
        assert "content" in prompt
        assert "empty" not in prompt


# ── Summarization ──────────────────────────────────────────

class TestSummarization:
    async def test_summarize_success(self):
        mgr = _make_mgr()
        mock_response = MagicMock()
        mock_response.text = "Summary of conversation"
        mgr.provider.chat = AsyncMock(return_value=mock_response)

        messages = make_conversation(5)
        result = await mgr._summarize(messages)
        assert len(result) == 1
        assert result[0].role == "system"
        assert "Summary of conversation" in result[0].content

    async def test_summarize_empty(self):
        mgr = _make_mgr()
        result = await mgr._summarize([])
        assert result == []

    async def test_summarize_timeout_uses_fallback(self):
        mgr = _make_mgr(compress_timeout=1)
        mgr.provider.chat = AsyncMock(side_effect=asyncio.TimeoutError)

        fb = AsyncMock()
        fb.name = "fallback"
        mock_resp = MagicMock()
        mock_resp.text = "Fallback summary"
        fb.chat = AsyncMock(return_value=mock_resp)
        mgr.fallback_provider = fb

        messages = make_conversation(3)
        result = await mgr._summarize(messages)
        assert "Fallback summary" in result[0].content
        fb.chat.assert_called_once()

    async def test_summarize_all_fail_raises(self):
        mgr = _make_mgr(compress_timeout=1)
        mgr.provider.chat = AsyncMock(side_effect=Exception("primary fail"))
        mgr.fallback_provider = None

        messages = make_conversation(3)
        with pytest.raises(RuntimeError, match="All compression providers failed"):
            await mgr._summarize(messages)


# ── Hybrid compress end-to-end ─────────────────────────────

class TestHybridCompress:
    async def test_hybrid_splits_old_and_recent(self):
        mgr = _make_mgr(recent_rounds_keep=3)
        mock_resp = MagicMock()
        mock_resp.text = "Summary of old messages"
        mgr.provider.chat = AsyncMock(return_value=mock_resp)

        messages = make_conversation(10)
        result = await mgr.compress(messages, strategy="hybrid")
        # Should contain: summary + recent 3 rounds (6 messages)
        contents = [m.content for m in result]
        assert any("Summary" in c for c in contents)
        assert "Question 9" in contents

    async def test_hybrid_too_short_returns_original(self):
        mgr = _make_mgr(recent_rounds_keep=5)
        messages = make_conversation(3)  # 6 messages < 5*2=10
        result = await mgr.compress(messages, strategy="hybrid")
        assert result == messages

    async def test_sliding_window_strategy(self):
        mgr = _make_mgr(recent_rounds_keep=2)
        messages = make_conversation(10)
        result = await mgr.compress(messages, strategy="sliding_window")
        assert len(result) == 4  # 2 rounds = 4 messages
        assert result[0].content == "Question 8"
