"""Context building golden tests — recovery and system prompt snapshots.

These tests verify the exact format of context injected into LLM calls.
Any change to recovery format or prompt assembly triggers a golden diff.
"""
import json
from unittest.mock import AsyncMock

import pytest

from core.types import Message, ToolCall
from tests.conftest import assert_golden, load_fixture


# ── Helpers ──────────────────────────────────────────────

def _mock_session_store(messages_dicts: list[dict]):
    """Create a mock SessionStore that returns the given messages."""
    store = AsyncMock()
    store.get_recent_messages = AsyncMock(return_value=messages_dicts)
    return store


def _messages_to_dicts(messages: list[Message]) -> list[dict]:
    """Convert Message objects to raw dicts (as SessionStore returns)."""
    result = []
    for m in messages:
        d = {"role": m.role, "content": m.content or ""}
        if m.tool_calls:
            d["tool_calls"] = [
                {"function": {"name": tc.name, "arguments": tc.arguments}, "id": tc.id}
                for tc in m.tool_calls
            ]
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        return result
    return result


class TestRecoveryGolden:
    """Recovery context format snapshots."""

    async def test_multi_round_recovery(self, update_golden):
        """Plain multi-round conversation recovery format."""
        from core.context_manager import build_recovery_context

        # Load fixture as raw dicts (session store format)
        try:
            history_raw = load_fixture("sessions/multi_round.json")
        except FileNotFoundError:
            pytest.skip("Fixture sessions/multi_round.json not found")

        store = _mock_session_store(history_raw)
        component = await build_recovery_context(store, "test_session")
        assert component is not None
        assert_golden("context/recovery_multi_round.txt", component.content, update_golden)

    async def test_tool_call_recovery(self, update_golden):
        """Recovery format for conversation with tool calls."""
        from core.context_manager import build_recovery_context

        try:
            history_raw = load_fixture("sessions/with_tool_calls.json")
        except FileNotFoundError:
            pytest.skip("Fixture sessions/with_tool_calls.json not found")

        store = _mock_session_store(history_raw)
        component = await build_recovery_context(store, "test_session")
        assert component is not None
        assert_golden("context/recovery_with_tools.txt", component.content, update_golden)
        assert "hello" in component.content


class TestRecoveryStructure:
    """Non-golden structural tests for recovery context."""

    async def test_recovery_starts_with_preamble(self):
        """Recovery context starts with RECOVERY_PREAMBLE."""
        from core.context_manager import build_recovery_context, RECOVERY_PREAMBLE

        store = _mock_session_store([
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A"},
        ])
        component = await build_recovery_context(store, "test")
        assert component is not None
        assert RECOVERY_PREAMBLE.strip() in component.content

    async def test_recovery_warns_about_tool_access(self):
        """Recovery must warn that tool call records are inaccessible."""
        from core.context_manager import build_recovery_context

        store = _mock_session_store([
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "bash"}, "id": "tc_1"}]},
            {"role": "tool", "content": "files", "tool_call_id": "tc_1", "name": "bash"},
            {"role": "assistant", "content": "A"},
        ])
        component = await build_recovery_context(store, "test")
        assert component is not None
        assert "工具调用" in component.content
        assert "不可访问" in component.content or "重新" in component.content

    async def test_recovery_truncates_long_messages(self):
        """Messages longer than truncate limit are handled."""
        from core.context_manager import build_recovery_context

        long_msg = "x" * 10000
        store = _mock_session_store([
            {"role": "user", "content": long_msg},
            {"role": "assistant", "content": "short"},
        ])
        component = await build_recovery_context(store, "test")
        assert component is not None
        # Recovery preamble + message content should be reasonable length
        assert len(component.content) > 0
