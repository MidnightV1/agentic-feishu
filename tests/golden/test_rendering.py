"""Blocks rendering golden tests — Markdown → Feishu Block JSON snapshots.

Each test converts a Markdown string to Feishu document blocks and compares
against a golden JSON file. Run with --update-golden to regenerate.
"""
import json

import pytest

from tests.conftest import assert_golden


CASES = [
    ("plain_text", "Hello World"),
    ("heading_h1", "# 一级标题"),
    ("heading_h2", "## 二级标题"),
    ("heading_h3", "### 三级标题"),
    ("code_python", "```python\ndef hello():\n    print('world')\n```"),
    ("code_bash", "```bash\necho hello\nls -la\n```"),
    ("code_no_lang", "```\nplain code block\n```"),
    ("bullet_list", "- item 1\n- item 2\n- item 3"),
    ("ordered_list", "1. first\n2. second\n3. third"),
    ("table_2x3", "| Name | Value |\n|------|-------|\n| A | 1 |\n| B | 2 |"),
    ("table_wide", "| A | B | C | D | E | F | G | H | I |\n" +
     "|---|---|---|---|---|---|---|---|---|\n" +
     "| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |"),
    ("divider", "---"),
    ("blockquote", "> This is a quote\n> with two lines"),
    ("inline_bold", "This is **bold** text"),
    ("inline_code", "Use `print()` function"),
    ("inline_link", "Visit [Google](https://www.google.com)"),
    ("inline_mixed", "**Bold** and `code` and [link](https://x.com)"),
    ("mixed_content",
     "# Title\n\nParagraph text here.\n\n"
     "- bullet 1\n- bullet 2\n\n"
     "```python\nprint('hello')\n```\n\n"
     "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
     "> Quote\n\n"
     "---\n\n"
     "Final paragraph with **bold**."),
    ("empty", ""),
    ("whitespace_only", "   \n\n  \n"),
    ("chinese_mixed", "# 项目概述\n\n这是一个**重要**的项目。\n\n- 功能一\n- 功能二"),
]


@pytest.mark.parametrize("name,markdown", CASES)
def test_blocks_rendering(name, markdown, update_golden):
    from platforms.feishu.blocks import text_to_blocks

    blocks = text_to_blocks(markdown)
    output = json.dumps(blocks, indent=2, ensure_ascii=False)
    assert_golden(f"rendering/blocks_{name}.json", output, update_golden)


class TestBlocksEdgeCases:
    """Non-golden edge case tests."""

    def test_table_exceeds_max_cols(self):
        """Table with >9 columns should be truncated or split."""
        from platforms.feishu.blocks import text_to_blocks

        cols = " | ".join([f"C{i}" for i in range(12)])
        sep = " | ".join(["---"] * 12)
        vals = " | ".join([f"V{i}" for i in range(12)])
        md = f"| {cols} |\n| {sep} |\n| {vals} |"

        blocks = text_to_blocks(md)
        # Should not crash; table should be present (possibly truncated)
        assert blocks is not None

    def test_empty_code_block(self):
        """Empty code block should not crash (Feishu API rejects empty)."""
        from platforms.feishu.blocks import text_to_blocks

        blocks = text_to_blocks("```\n```")
        assert blocks is not None

    def test_unclosed_code_block(self):
        """Unclosed code block handled gracefully."""
        from platforms.feishu.blocks import text_to_blocks

        blocks = text_to_blocks("```python\nprint('hello')")
        assert blocks is not None

    def test_card_directive_stripped(self):
        """{{card:header=...}} stripped in document context."""
        from platforms.feishu.blocks import text_to_blocks

        blocks = text_to_blocks("{{card:header=Test,color=blue}}\nContent")
        # Card directive should not appear in document blocks
        output = json.dumps(blocks)
        assert "{{card:" not in output
