"""Context manager tests — compression, token counting, tool pair integrity.

Covers:
- P0-5: compression must not split tool_call/tool_result pairs
- CTX-2: token counting must include tool definitions
- Compression trigger threshold
- Hybrid strategy: recent rounds preserved, older compressed
- Fallback on compression failure
- D9: recovery uses 15 rounds (not 15 messages)
"""
import pytest
from unittest.mock import AsyncMock

from core.types import Message, ToolCall


# ── Helpers ──────────────────────────────────────────────

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
            # Tool call round: assistant(tc) → tool result → assistant(reply)
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
        from core.context_manager import ContextManager

        mgr = ContextManager(context_window=40000, compress_threshold=0.8)
        mgr.count_tokens = AsyncMock(return_value=33000)  # > 32000

        messages = make_conversation(20)
        assert await mgr.should_compress(messages) is True

    async def test_no_compress_below_threshold(self):
        from core.context_manager import ContextManager

        mgr = ContextManager(context_window=40000, compress_threshold=0.8)
        mgr.count_tokens = AsyncMock(return_value=20000)  # < 32000

        messages = make_conversation(5)
        assert await mgr.should_compress(messages) is False


# ── Tool call/result pair integrity (P0-5) ───────────────

class TestToolPairIntegrity:
    """P0-5: Compression must never split assistant(tool_calls) from tool results."""

    def test_split_keeps_pairs_together(self):
        """When splitting old/recent, tool_call and tool_result stay in same group."""
        from core.context_manager import ContextManager

        mgr = ContextManager(context_window=40000)
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

        # Split keeping last 1 round
        old, recent = mgr._split_for_compress(messages, keep_recent_rounds=1)

        # Verify: in 'old', every assistant with tool_calls has its tool results
        for i, msg in enumerate(old):
            if msg.role == "assistant" and msg.tool_calls:
                # Next message(s) must be tool results for all tool_calls
                expected_ids = {tc.id for tc in msg.tool_calls}
                j = i + 1
                found_ids = set()
                while j < len(old) and old[j].role == "tool":
                    found_ids.add(old[j].tool_call_id)
                    j += 1
                assert expected_ids == found_ids, \
                    f"Tool pair broken in 'old': expected {expected_ids}, found {found_ids}"

        # Same check for 'recent'
        for i, msg in enumerate(recent):
            if msg.role == "assistant" and msg.tool_calls:
                expected_ids = {tc.id for tc in msg.tool_calls}
                j = i + 1
                found_ids = set()
                while j < len(recent) and recent[j].role == "tool":
                    found_ids.add(recent[j].tool_call_id)
                    j += 1
                assert expected_ids == found_ids, \
                    f"Tool pair broken in 'recent': expected {expected_ids}, found {found_ids}"

    def test_multiple_tool_calls_in_one_turn(self):
        """Assistant with 2 tool_calls → both results must stay together."""
        from core.context_manager import ContextManager

        mgr = ContextManager(context_window=40000)
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

        old, recent = mgr._split_for_compress(messages, keep_recent_rounds=1)

        # The 4-message tool block (assistant+2tools+assistant) must be intact
        tc_msg = [m for m in old if m.role == "assistant" and m.tool_calls]
        if tc_msg:
            idx = old.index(tc_msg[0])
            assert old[idx + 1].role == "tool"
            assert old[idx + 2].role == "tool"


# ── Hybrid strategy ──────────────────────────────────────

class TestHybridStrategy:

    async def test_recent_rounds_preserved(self):
        """Hybrid keeps last N rounds as raw text."""
        from core.context_manager import ContextManager

        mgr = ContextManager(context_window=40000, recent_rounds_keep=5)
        messages = make_conversation(20)

        old, recent = mgr._split_for_compress(messages, keep_recent_rounds=5)
        # Last 5 rounds = 10 messages
        assert len(recent) == 10
        assert recent[0].content == "Question 15"

    async def test_compression_fallback(self):
        """Summary failure → fallback to sliding_window."""
        from core.context_manager import ContextManager

        mock_summarizer = AsyncMock(side_effect=Exception("LLM timeout"))
        mgr = ContextManager(context_window=40000)
        mgr._summarize = mock_summarizer

        messages = make_conversation(20)
        result = await mgr.compress(messages, strategy="summary")
        # Should not raise, should fallback
        assert result is not None


# ── Token counting (CTX-2) ───────────────────────────────

class TestTokenCounting:

    async def test_count_includes_tools(self):
        """CTX-2: token count must include tool schema tokens."""
        from core.context_manager import ContextManager

        mgr = ContextManager(context_window=40000)
        messages = [Message(role="user", content="hello")]
        tools = [{"type": "function", "function": {
            "name": "bash",
            "description": "Run a command" * 100,  # Large description
            "parameters": {"type": "object", "properties": {
                "command": {"type": "string", "description": "The command to run"},
            }},
        }}]

        count_with = await mgr.count_tokens(messages, tools=tools)
        count_without = await mgr.count_tokens(messages, tools=None)
        assert count_with > count_without, \
            "Token count with tools must be higher than without"


# ── Recovery context (D9) ────────────────────────────────

class TestRecoveryContext:
    """D9: recovery uses 15 rounds, not 15 messages."""

    async def test_recovery_15_rounds(self):
        from core.context_manager import ContextManager

        mgr = ContextManager(context_window=40000)
        # 20 rounds = 40 messages
        messages = make_conversation(20)

        recovery = mgr.build_recovery_context(messages, recent_rounds=15)
        # Should contain last 15 rounds worth of content
        assert "Question 5" in recovery  # Round 6 (first of last 15)
        assert "Question 19" in recovery  # Last round
        assert "Question 0" not in recovery  # Too old, excluded

    async def test_recovery_with_tool_rounds(self):
        """A round with tool calls counts as 1 round."""
        from core.context_manager import ContextManager

        mgr = ContextManager(context_window=40000)
        messages = make_conversation_with_tools(10)

        recovery = mgr.build_recovery_context(messages, recent_rounds=5)
        # Last 5 rounds should be included
        assert "Request 9" in recovery
        assert "Request 5" in recovery
