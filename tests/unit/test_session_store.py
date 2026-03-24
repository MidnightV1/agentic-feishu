"""Session store tests — persistence, tool_call_id storage, round-based recovery.

Covers:
- CTX-6: tool_call_id and tool_name must be stored and retrievable
- D9: get_recent_messages by rounds (not messages)
- Round counting with tool calls
- Message truncation on recovery
"""
import pytest


class TestBasicStorage:
    """Store and retrieve messages."""

    async def test_add_and_get(self, session_store):
        await session_store.add_message("s1", role="user", content="hello")
        await session_store.add_message("s1", role="assistant", content="hi")
        messages = await session_store.get_messages("s1")
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    async def test_separate_sessions(self, session_store):
        await session_store.add_message("s1", role="user", content="session 1")
        await session_store.add_message("s2", role="user", content="session 2")
        m1 = await session_store.get_messages("s1")
        m2 = await session_store.get_messages("s2")
        assert len(m1) == 1
        assert len(m2) == 1
        assert m1[0]["content"] == "session 1"


class TestToolCallStorage:
    """CTX-6: tool_call_id and name must survive storage roundtrip."""

    async def test_store_tool_calls(self, session_store):
        """Assistant message with tool_calls stored and retrieved."""
        await session_store.add_message("s1", role="assistant", content="",
            tool_calls=[{"id": "tc_1", "name": "bash", "arguments": {"cmd": "ls"}}])
        messages = await session_store.get_messages("s1")
        assert messages[0]["tool_calls"] is not None
        assert messages[0]["tool_calls"][0]["id"] == "tc_1"

    async def test_store_tool_result(self, session_store):
        """Tool result message with tool_call_id and name stored and retrieved."""
        await session_store.add_message("s1", role="tool", content="file.py",
            tool_call_id="tc_1", name="bash")
        messages = await session_store.get_messages("s1")
        assert messages[0]["tool_call_id"] == "tc_1"
        assert messages[0]["name"] == "bash"

    async def test_full_tool_roundtrip(self, session_store):
        """Complete tool call cycle: assistant(tc) → tool(result) → assistant(reply)."""
        await session_store.add_message("s1", role="user", content="list files")
        await session_store.add_message("s1", role="assistant", content="",
            tool_calls=[{"id": "tc_1", "name": "bash", "arguments": {"cmd": "ls"}}])
        await session_store.add_message("s1", role="tool", content="a.py\nb.py",
            tool_call_id="tc_1", name="bash")
        await session_store.add_message("s1", role="assistant",
            content="Found a.py and b.py.")

        messages = await session_store.get_messages("s1")
        assert len(messages) == 4

        # Verify tool call in assistant message
        assert messages[1]["tool_calls"][0]["name"] == "bash"

        # Verify tool result has call_id and name
        assert messages[2]["tool_call_id"] == "tc_1"
        assert messages[2]["name"] == "bash"


class TestRoundBasedRecovery:
    """D9: Recovery retrieves N rounds, not N messages."""

    async def test_simple_rounds(self, session_store):
        """20 rounds → get_recent(rounds=15) → 30 messages (15 rounds x 2)."""
        for i in range(20):
            await session_store.add_message("s1", role="user", content=f"Q{i}")
            await session_store.add_message("s1", role="assistant", content=f"A{i}")

        messages = await session_store.get_recent_messages("s1", rounds=15)
        users = [m for m in messages if m["role"] == "user"]
        assert len(users) == 15
        # First included should be Q5 (round 6, skipping first 5)
        assert users[0]["content"] == "Q5"

    async def test_tool_call_round_counted_as_one(self, session_store):
        """A round with tool calls (4 messages) counts as 1 round."""
        # Round 1: simple
        await session_store.add_message("s1", role="user", content="Q1")
        await session_store.add_message("s1", role="assistant", content="A1")
        # Round 2: with tool call (4 messages)
        await session_store.add_message("s1", role="user", content="Search files")
        await session_store.add_message("s1", role="assistant", content="",
            tool_calls=[{"id": "tc_1", "name": "bash"}])
        await session_store.add_message("s1", role="tool", content="found.py",
            tool_call_id="tc_1", name="bash")
        await session_store.add_message("s1", role="assistant", content="Found it")
        # Round 3: simple
        await session_store.add_message("s1", role="user", content="Q3")
        await session_store.add_message("s1", role="assistant", content="A3")

        messages = await session_store.get_recent_messages("s1", rounds=2)
        # Last 2 rounds: round 2 (4 msgs) + round 3 (2 msgs) = 6 messages
        assert len(messages) == 6
        assert messages[0]["content"] == "Search files"

    async def test_truncation(self, session_store):
        """Messages truncated to max chars on recovery."""
        long_content = "x" * 10000
        await session_store.add_message("s1", role="user", content=long_content)
        await session_store.add_message("s1", role="assistant", content="short")

        messages = await session_store.get_recent_messages(
            "s1", rounds=1, truncate=4000)
        # Truncated content may include '...[truncated]' suffix
        assert len(messages[0]["content"]) <= 4020  # 4000 + suffix


class TestMessageDeletion:
    """P2-1: Delete recent messages (for recall)."""

    async def test_delete_last_round(self, session_store):
        await session_store.add_message("s1", role="user", content="Q1")
        await session_store.add_message("s1", role="assistant", content="A1")
        await session_store.add_message("s1", role="user", content="Q2")
        await session_store.add_message("s1", role="assistant", content="A2")

        await session_store.delete_recent_messages("s1", count=2)
        messages = await session_store.get_messages("s1")
        assert len(messages) == 2
        assert messages[-1]["content"] == "A1"
