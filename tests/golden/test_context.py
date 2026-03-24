"""Context building golden tests — recovery and system prompt snapshots.

These tests verify the exact format of context injected into LLM calls.
Any change to recovery format or prompt assembly triggers a golden diff.
"""
import json

import pytest

from core.types import Message, ToolCall
from tests.conftest import assert_golden, load_fixture


class TestRecoveryGolden:
    """Recovery context format snapshots."""

    def test_multi_round_recovery(self, update_golden):
        """Plain multi-round conversation recovery format."""
        from core.context_manager import ContextManager

        history_raw = load_fixture("sessions/multi_round.json")
        messages = [Message(**m) for m in history_raw]
        mgr = ContextManager(context_window=40000)

        result = mgr.build_recovery_context(messages, recent_rounds=15)
        assert_golden("context/recovery_multi_round.txt", result, update_golden)

    def test_tool_call_recovery(self, update_golden):
        """Recovery format for conversation with tool calls."""
        from core.context_manager import ContextManager

        history_raw = load_fixture("sessions/with_tool_calls.json")
        messages = [Message(**m) for m in history_raw]
        mgr = ContextManager(context_window=40000)

        result = mgr.build_recovery_context(messages, recent_rounds=15)
        assert_golden("context/recovery_with_tools.txt", result, update_golden)
        # Tool call results should be visible in recovery
        assert "print('hello')" in result or "hello" in result


class TestRecoveryStructure:
    """Non-golden structural tests for recovery context."""

    def test_recovery_starts_with_preamble(self):
        """Recovery context starts with RECOVERY_PREAMBLE."""
        from core.context_manager import ContextManager, RECOVERY_PREAMBLE

        messages = [
            Message(role="user", content="Q"),
            Message(role="assistant", content="A"),
        ]
        mgr = ContextManager(context_window=40000)
        result = mgr.build_recovery_context(messages, recent_rounds=15)
        assert result.startswith(RECOVERY_PREAMBLE) or \
               RECOVERY_PREAMBLE.strip() in result

    def test_recovery_warns_about_tool_access(self):
        """Recovery must warn that tool call records are inaccessible."""
        from core.context_manager import ContextManager

        messages = [
            Message(role="user", content="Q"),
            Message(role="assistant", content="", tool_calls=[
                ToolCall(id="tc_1", name="bash", arguments={"cmd": "ls"}),
            ]),
            Message(role="tool", content="files", tool_call_id="tc_1", name="bash"),
            Message(role="assistant", content="A"),
        ]
        mgr = ContextManager(context_window=40000)
        result = mgr.build_recovery_context(messages, recent_rounds=15)
        assert "工具调用" in result
        assert "不可访问" in result or "无法访问" in result or "重新" in result

    def test_recovery_truncates_long_messages(self):
        """Messages longer than 4000 chars are truncated in recovery."""
        from core.context_manager import ContextManager

        long_msg = "x" * 10000
        messages = [
            Message(role="user", content=long_msg),
            Message(role="assistant", content="short"),
        ]
        mgr = ContextManager(context_window=40000)
        result = mgr.build_recovery_context(
            messages, recent_rounds=15, truncate=4000)
        assert len(result) < 10000  # Original would be >10000
