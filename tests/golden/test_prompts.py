"""Frozen prompt tests — any prompt change must be explicitly approved via --update-golden.

These tests ensure prompt integrity across code changes. Hub-derived prompts
that af must faithfully reproduce are locked as golden files.
"""
import pytest

from tests.conftest import assert_golden


class TestFrozenPrompts:
    """Prompt exact-match tests. Changes trigger golden diff for human review."""

    def test_summary_prompt(self, update_golden):
        """Compression prompt — 9 sections, preserves corrections and full paths."""
        from core.context_manager import SUMMARY_PROMPT
        assert_golden("prompts/summary_prompt.txt", SUMMARY_PROMPT, update_golden)
        # Core requirements: user corrections highest weight, no abbreviations
        assert "纠正" in SUMMARY_PROMPT or "偏好" in SUMMARY_PROMPT, \
            "SUMMARY_PROMPT must preserve user corrections and preferences"
        assert "文件路径" in SUMMARY_PROMPT, \
            "SUMMARY_PROMPT must preserve full file paths"

    def test_recovery_preamble(self, update_golden):
        """Session recovery preamble injected into system prompt."""
        from core.context_manager import RECOVERY_PREAMBLE
        assert_golden("prompts/recovery_preamble.txt", RECOVERY_PREAMBLE, update_golden)
        assert "工具调用" in RECOVERY_PREAMBLE
        assert "不可访问" in RECOVERY_PREAMBLE or "无法访问" in RECOVERY_PREAMBLE

    def test_recovery_recent_rounds(self):
        """D9: Recovery uses 15 rounds."""
        from core.context_manager import RECOVERY_RECENT_ROUNDS
        assert RECOVERY_RECENT_ROUNDS == 15

    def test_feishu_system_prompt(self, update_golden):
        """Feishu channel protocol prompt."""
        from platforms.feishu.prompts import FEISHU_SYSTEM_PROMPT
        assert_golden("prompts/feishu_system.txt", FEISHU_SYSTEM_PROMPT, update_golden)
        assert "<user-input>" in FEISHU_SYSTEM_PROMPT
        assert "<reply-to-user>" in FEISHU_SYSTEM_PROMPT
        assert "<next-explore>" in FEISHU_SYSTEM_PROMPT
        assert "{{card:header=" in FEISHU_SYSTEM_PROMPT

    def test_feishu_prompt_card_colors(self):
        """Card color semantic system — at least core colors documented."""
        from platforms.feishu.prompts import FEISHU_SYSTEM_PROMPT
        # Core colors that must be present
        for color in ["blue", "green", "orange", "red"]:
            assert color in FEISHU_SYSTEM_PROMPT, f"Missing core card color: {color}"
        # TODO: af should also document grey, purple, indigo, yellow per hub spec


class TestPromptStructure:
    """Non-frozen structural tests."""

    def test_summary_prompt_has_required_sections(self):
        from core.context_manager import SUMMARY_PROMPT
        for section in ["主要请求", "关键技术", "文件与代码", "错误与修复", "待完成"]:
            assert section in SUMMARY_PROMPT, f"Missing section: {section}"

    def test_recovery_preamble_no_cli_reference(self):
        """AF recovery should not reference 'CLI session' (hub-specific)."""
        from core.context_manager import RECOVERY_PREAMBLE
        assert "CLI" not in RECOVERY_PREAMBLE
