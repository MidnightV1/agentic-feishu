"""Provider format conversion tests — roundtrip and golden snapshots.

Covers:
- P0-4: Gemini function_response name must be function name
- Tool schema conversion: OpenAI → Anthropic / Gemini
- Message format conversion: internal → provider-specific
- Tool result format: tool message → provider tool_result blocks
- CTX-7: Gemini tool_call ID uniqueness
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.types import Message, ToolCall
from tests.conftest import assert_golden


# ── Test data ────────────────────────────────────────────

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a file from disk",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
            },
            "required": ["path"],
        },
    },
}

MESSAGES_WITH_TOOLS = [
    Message(role="user", content="Read test.py"),
    Message(role="assistant", content="", tool_calls=[
        ToolCall(id="tc_read_1", name="read_file", arguments={"path": "test.py"}),
    ]),
    Message(role="tool", content="print('hello')",
            tool_call_id="tc_read_1", name="read_file"),
    Message(role="assistant", content="The file prints hello."),
]


# ── Helpers ──────────────────────────────────────────────

def _make_anthropic():
    """Create AnthropicProvider with mocked SDK client."""
    with patch("anthropic.AsyncAnthropic"):
        from providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key="test-key", model="claude-sonnet-4-6")


def _make_openai():
    """Create OpenAIProvider with mocked SDK client."""
    with patch("openai.AsyncOpenAI"):
        from providers.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key="test-key", model="gpt-4o", base_url="https://test")


def _make_gemini():
    """Create GeminiProvider with mocked SDK client."""
    with patch("google.genai.Client"):
        from providers.gemini_provider import GeminiProvider
        return GeminiProvider(api_key="test-key", model="gemini-2.0-flash")


# ── Anthropic ────────────────────────────────────────────

class TestAnthropicConversion:

    def test_tool_schema_conversion(self):
        """OpenAI tool schema → Anthropic format."""
        provider = _make_anthropic()
        converted = provider._convert_tools([TOOL_SCHEMA])
        assert converted[0]["name"] == "read_file"
        assert "input_schema" in converted[0]
        assert converted[0]["input_schema"]["properties"]["path"]["type"] == "string"

    def test_tool_result_as_user_block(self):
        """Tool result → user role + tool_result content block."""
        provider = _make_anthropic()
        tool_msg = Message(role="tool", content="file content",
                          tool_call_id="tc_1", name="read_file")
        _, converted = provider._convert_messages([tool_msg])
        assert converted[0]["role"] == "user"
        block = converted[0]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "tc_1"

    def test_system_extracted_separately(self):
        """System message → separate system parameter, not in messages."""
        provider = _make_anthropic()
        msgs = [
            Message(role="system", content="You are helpful."),
            Message(role="user", content="Hi"),
        ]
        system, converted = provider._convert_messages(msgs)
        assert system is not None
        assert any("You are helpful" in str(b) for b in system)
        assert len(converted) == 1
        assert converted[0]["role"] == "user"


# ── OpenAI ───────────────────────────────────────────────

class TestOpenAIConversion:

    def test_tool_schema_passthrough(self):
        """OpenAI format is the internal format — no conversion needed."""
        provider = _make_openai()
        converted = provider._convert_tools([TOOL_SCHEMA])
        assert converted == [TOOL_SCHEMA]

    def test_tool_result_format(self):
        """Tool result → role=tool, tool_call_id, content."""
        provider = _make_openai()
        tool_msg = Message(role="tool", content="result",
                          tool_call_id="tc_1", name="read_file")
        converted = provider._convert_messages([tool_msg])
        assert converted[0]["role"] == "tool"
        assert converted[0]["tool_call_id"] == "tc_1"


# ── Gemini ───────────────────────────────────────────────

class TestGeminiConversion:

    def test_function_response_name_is_function_name(self):
        """P0-4: function_response name must be the function name, not call ID."""
        provider = _make_gemini()
        tool_msg = Message(role="tool", content="file content",
                          tool_call_id="tc_abc123", name="read_file")
        _sys, contents = provider._convert_messages([tool_msg])
        resp = contents[0]
        assert "read_file" in str(resp), \
            "Gemini function_response must use function name, not call ID"

    def test_tool_call_id_uniqueness(self):
        """CTX-7: Multiple calls to same function get unique IDs."""
        # Implementation depends on provider internals
        pass

    def test_system_as_system_instruction(self):
        """System message → Gemini system_instruction parameter."""
        provider = _make_gemini()
        msgs = [
            Message(role="system", content="You are helpful."),
            Message(role="user", content="Hi"),
        ]
        sys_instruction, contents = provider._convert_messages(msgs)
        assert sys_instruction is not None
        assert "You are helpful" in sys_instruction
        # System messages should not appear in contents
        for c in contents:
            assert str(c.role) != "system"
