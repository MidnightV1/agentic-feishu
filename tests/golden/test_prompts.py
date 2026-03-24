"""Frozen prompt tests — any prompt change must be explicitly approved via --update-golden.

These tests ensure prompt integrity across code changes. Hub-derived prompts
that af must faithfully reproduce are locked as golden files.
"""
import pytest

from tests.conftest import assert_golden


class TestFrozenPrompts:
    """Prompt exact-match tests. Changes trigger golden diff for human review."""

    def test_summary_prompt(self, update_golden):
        """Compression prompt — must retain 'excluded X because Y' per D2."""
        from core.context_manager import SUMMARY_PROMPT
        assert_golden("prompts/summary_prompt.txt", SUMMARY_PROMPT, update_golden)
        # D2 decision: these hub lines must be present
        assert "排除" in SUMMARY_PROMPT, \
            "SUMMARY_PROMPT missing '排除了 X，因为 Y' (hub alignment per D2)"
        assert "代码修改只记录" in SUMMARY_PROMPT or "改了哪些文件" in SUMMARY_PROMPT, \
            "SUMMARY_PROMPT missing code-change summary rule (hub alignment per D2)"

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
        for section in ["对话主题", "关键决策与结果", "当前状态", "涉及的文件与变更", "用户偏好与纠正"]:
            assert section in SUMMARY_PROMPT, f"Missing section: {section}"

    def test_recovery_preamble_no_cli_reference(self):
        """AF recovery should not reference 'CLI session' (hub-specific)."""
        from core.context_manager import RECOVERY_PREAMBLE
        assert "CLI" not in RECOVERY_PREAMBLE
