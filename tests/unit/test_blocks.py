# -*- coding: utf-8 -*-
"""Unit tests for platforms.feishu.blocks — markdown → Feishu block conversion.

Adapted from claude-hub/tests/unit/test_utils.py TestTextToBlocks,
with structure adjustments for af's data model (tree-style _nested_list
vs hub's flat+depth style).
"""
import sys
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platforms.feishu.blocks import (
    text_to_blocks, split_table_rows, TABLE_MAX_COLS, TABLE_MAX_ROWS,
    _parse_inline, _strip_html_tags, _CODE_LANG_MAP,
)


# ── Basic block types ──


class TestHeadings:
    def test_h1(self):
        blocks = text_to_blocks("# 标题一")
        assert blocks[0]["block_type"] == 3
        assert "heading1" in blocks[0]
        assert blocks[0]["heading1"]["elements"][0]["text_run"]["content"] == "标题一"

    def test_h2_to_h6(self):
        for level in range(2, 7):
            blocks = text_to_blocks(f"{'#' * level} Heading {level}")
            assert blocks[0]["block_type"] == 2 + level
            assert f"heading{level}" in blocks[0]

    def test_heading_without_space_not_matched(self):
        """#NoSpace should be treated as plain text."""
        blocks = text_to_blocks("#NoSpace")
        assert blocks[0]["block_type"] == 2

    def test_inline_bold_in_heading(self):
        blocks = text_to_blocks("## **重要**标题")
        heading = blocks[0]["heading2"]["elements"]
        bold_elem = next(
            (e for e in heading if e["text_run"].get("text_element_style", {}).get("bold")),
            None,
        )
        assert bold_elem is not None
        assert bold_elem["text_run"]["content"] == "重要"


class TestParagraphs:
    def test_plain_text(self):
        blocks = text_to_blocks("普通文本内容")
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == 2
        assert blocks[0]["text"]["elements"][0]["text_run"]["content"] == "普通文本内容"

    def test_empty_lines_skipped(self):
        blocks = text_to_blocks("Line 1\n\n\nLine 2\n\n")
        assert len(blocks) == 2

    def test_consecutive_empty_lines(self):
        blocks = text_to_blocks("\n\n\n\nSome text\n\n\n")
        assert len(blocks) == 1

    def test_literal_backslash_n_replaced(self):
        blocks = text_to_blocks("Line 1\\nLine 2")
        assert len(blocks) == 2
        assert blocks[0]["text"]["elements"][0]["text_run"]["content"] == "Line 1"
        assert blocks[1]["text"]["elements"][0]["text_run"]["content"] == "Line 2"


class TestInlineFormatting:
    def test_bold(self):
        blocks = text_to_blocks("这是**粗体**文本")
        elements = blocks[0]["text"]["elements"]
        bold = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("bold")),
            None,
        )
        assert bold is not None
        assert bold["text_run"]["content"] == "粗体"

    def test_italic(self):
        blocks = text_to_blocks("这是*斜体*文本")
        elements = blocks[0]["text"]["elements"]
        italic = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("italic")),
            None,
        )
        assert italic is not None
        assert italic["text_run"]["content"] == "斜体"

    def test_inline_code(self):
        blocks = text_to_blocks("使用 `python3 main.py` 启动")
        elements = blocks[0]["text"]["elements"]
        code = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("inline_code")),
            None,
        )
        assert code is not None
        assert code["text_run"]["content"] == "python3 main.py"

    def test_link(self):
        blocks = text_to_blocks("参见 [文档](https://feishu.cn/docx/abc)")
        elements = blocks[0]["text"]["elements"]
        link = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("link")),
            None,
        )
        assert link is not None
        assert link["text_run"]["text_element_style"]["link"]["url"] == "https://feishu.cn/docx/abc"

    def test_special_chars_do_not_crash(self):
        text = "!@#$%^&*()[]{}|\\/<>?~`±§™©®€¥"
        blocks = text_to_blocks(text)
        assert len(blocks) == 1

    def test_unicode_emoji(self):
        blocks = text_to_blocks("✅ 部署成功 🚀 系统正常运行")
        assert len(blocks) == 1
        content = blocks[0]["text"]["elements"][0]["text_run"]["content"]
        assert "✅" in content
        assert "🚀" in content

    def test_chinese_english_mixed(self):
        md = "# 项目状态 Project Status\n\n混合内容 mixed content。\n\n- **关键** Key: `99.9%`"
        blocks = text_to_blocks(md)
        assert blocks[0]["block_type"] == 3  # H1
        assert blocks[1]["block_type"] == 2  # text
        assert blocks[2]["block_type"] == 12  # bullet


class TestCodeBlocks:
    def test_basic_code_block(self):
        md = "```python\ndef hello():\n    print('hi')\n```"
        blocks = text_to_blocks(md)
        assert len(blocks) == 1
        assert blocks[0]["block_type"] == 14
        assert blocks[0]["code"]["style"]["language"] == 49  # python

    def test_empty_code_block_gets_placeholder(self):
        blocks = text_to_blocks("```\n```")
        assert blocks[0]["block_type"] == 14
        content = blocks[0]["code"]["elements"][0]["text_run"]["content"]
        assert len(content) > 0  # Feishu rejects empty

    def test_code_multiline_preserved(self):
        code = "def foo():\n    return 42\n\n\nprint(foo())"
        blocks = text_to_blocks(f"```python\n{code}\n```")
        content = blocks[0]["code"]["elements"][0]["text_run"]["content"]
        assert "def foo():" in content
        assert "return 42" in content
        assert "print(foo())" in content

    def test_unclosed_code_block(self):
        md = "```python\nprint('unclosed')"
        blocks = text_to_blocks(md)
        assert blocks[0]["block_type"] == 14
        assert "unclosed" in blocks[0]["code"]["elements"][0]["text_run"]["content"]


class TestLists:
    def test_bullet_dash(self):
        blocks = text_to_blocks("- 第一项\n- 第二项")
        assert len(blocks) == 2
        assert all(b["block_type"] == 12 for b in blocks)
        assert blocks[0]["bullet"]["elements"][0]["text_run"]["content"] == "第一项"

    def test_bullet_asterisk(self):
        blocks = text_to_blocks("* item A\n* item B")
        assert all(b["block_type"] == 12 for b in blocks)

    def test_ordered(self):
        blocks = text_to_blocks("1. First\n2. Second\n3. Third")
        assert len(blocks) == 3
        assert all(b["block_type"] == 13 for b in blocks)
        assert blocks[0]["ordered"]["elements"][0]["text_run"]["content"] == "First"


class TestBlockquotes:
    def test_basic(self):
        blocks = text_to_blocks("> 引用内容")
        assert "_quote" in blocks[0]
        quote_children = blocks[0]["_quote"]
        assert len(quote_children) == 1
        assert quote_children[0]["block_type"] == 2
        all_text = "".join(
            e["text_run"]["content"]
            for e in quote_children[0]["text"]["elements"]
        )
        assert "引用内容" in all_text

    def test_empty_quote(self):
        blocks = text_to_blocks(">")
        assert "_quote" in blocks[0]
        quote_children = blocks[0]["_quote"]
        assert quote_children[0]["block_type"] == 2

    def test_consecutive_quotes_grouped(self):
        """Consecutive > lines should group into a single _quote container."""
        blocks = text_to_blocks("> 第一行\n> 第二行\n> 第三行")
        assert len(blocks) == 1
        assert "_quote" in blocks[0]
        assert len(blocks[0]["_quote"]) == 3
        texts = [
            "".join(e["text_run"]["content"] for e in child["text"]["elements"])
            for child in blocks[0]["_quote"]
        ]
        assert "第一行" in texts[0]
        assert "第二行" in texts[1]
        assert "第三行" in texts[2]

    def test_html_entities_decoded(self):
        """HTML entities should be decoded."""
        blocks = text_to_blocks("> a &#60; b &#62; c")
        assert "_quote" in blocks[0]
        all_text = "".join(
            e["text_run"]["content"]
            for e in blocks[0]["_quote"][0]["text"]["elements"]
        )
        assert "a < b > c" in all_text


class TestDividers:
    def test_triple_dash(self):
        blocks = text_to_blocks("---")
        assert blocks[0]["block_type"] == 22
        assert "divider" in blocks[0]

    def test_hr_tag(self):
        blocks = text_to_blocks("<hr>")
        assert blocks[0]["block_type"] == 22


class TestTables:
    def test_basic_table(self):
        md = "| 列1 | 列2 |\n|-----|-----|\n| 值1 | 值2 |"
        blocks = text_to_blocks(md)
        assert len(blocks) == 1
        assert "_table" in blocks[0]
        table = blocks[0]["_table"]
        assert table[0] == ["列1", "列2"]
        assert table[1] == ["值1", "值2"]

    def test_wide_table_not_truncated_by_parser(self):
        """Parser returns all columns; truncation is done in append_document."""
        header = "| " + " | ".join([f"C{i}" for i in range(10)]) + " |"
        sep = "| " + " | ".join(["---"] * 10) + " |"
        row = "| " + " | ".join([str(i) for i in range(10)]) + " |"
        blocks = text_to_blocks(f"{header}\n{sep}\n{row}")
        assert "_table" in blocks[0]
        assert len(blocks[0]["_table"][0]) == 10

    def test_multiple_tables(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |\n\n| X | Y | Z |\n|---|---|---|\n| a | b | c |"
        blocks = text_to_blocks(md)
        tables = [b for b in blocks if "_table" in b]
        assert len(tables) == 2


class TestSanitization:
    def test_control_chars_stripped(self):
        blocks = text_to_blocks("正常文字\x00隐藏内容")
        content = blocks[0]["text"]["elements"][0]["text_run"]["content"]
        assert "\x00" not in content


# ── Nested lists (af tree structure: {block, children}) ──


class TestNestedLists:
    """_collect_nested_list returns flat items with depth:
    [{"type": "bullet"|"ordered", "depth": N, "elements": [...]}]
    """

    def test_2_level_unordered(self):
        """Two-level nested bullet list produces _nested_list marker."""
        md = "- 第一层\n  - 第二层子项\n- 回到第一层"
        blocks = text_to_blocks(md)
        assert len(blocks) == 1
        assert "_nested_list" in blocks[0]
        items = blocks[0]["_nested_list"]
        assert len(items) == 3
        assert items[0] == {"type": "bullet", "depth": 0, "elements": [{"text_run": {"content": "第一层"}}]}
        assert items[1]["type"] == "bullet"
        assert items[1]["depth"] == 1
        assert items[1]["elements"][0]["text_run"]["content"] == "第二层子项"
        assert items[2]["depth"] == 0

    def test_3_level_unordered(self):
        """Three-level nesting preserves all depth levels."""
        md = "- L1\n  - L2\n    - L3"
        blocks = text_to_blocks(md)
        assert "_nested_list" in blocks[0]
        items = blocks[0]["_nested_list"]
        assert len(items) == 3
        assert items[0]["depth"] == 0
        assert items[1]["depth"] == 1
        assert items[2]["depth"] == 2
        assert items[2]["elements"][0]["text_run"]["content"] == "L3"

    def test_2_level_ordered(self):
        md = "1. 步骤一\n  1. 子步骤A\n  2. 子步骤B\n2. 步骤二"
        blocks = text_to_blocks(md)
        assert "_nested_list" in blocks[0]
        items = blocks[0]["_nested_list"]
        assert len(items) == 4
        assert items[0] == {"type": "ordered", "depth": 0, "elements": [{"text_run": {"content": "步骤一"}}]}
        assert items[1]["type"] == "ordered"
        assert items[1]["depth"] == 1
        assert items[1]["elements"][0]["text_run"]["content"] == "子步骤A"

    def test_flat_list_no_nesting_marker(self):
        """Non-nested list should NOT produce _nested_list marker."""
        blocks = text_to_blocks("- 项目A\n- 项目B\n- 项目C")
        assert all("_nested_list" not in b for b in blocks)
        assert all(b["block_type"] == 12 for b in blocks)

    def test_complex_mixed_nesting(self):
        md = "- 功能模块\n  - 用户系统\n  - 数据管理\n- 非功能需求"
        blocks = text_to_blocks(md)
        assert "_nested_list" in blocks[0]
        items = blocks[0]["_nested_list"]
        assert len(items) == 4
        assert [i["depth"] for i in items] == [0, 1, 1, 0]
        assert items[0]["elements"][0]["text_run"]["content"] == "功能模块"
        assert items[3]["elements"][0]["text_run"]["content"] == "非功能需求"


# ── Mixed content ordering ──


class TestMixedContent:
    def test_all_block_types_ordering(self):
        md = (
            "# 标题\n"
            "---\n"
            "```python\ncode\n```\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n"
            "- 列表项\n"
            "1. 有序项\n"
            "> 引用\n"
            "普通文本\n"
        )
        blocks = text_to_blocks(md)
        def block_type(b):
            if "_table" in b: return "_table"
            if "_quote" in b: return "_quote"
            return b.get("block_type")
        types = [block_type(b) for b in blocks]
        assert types[0] == 3   # heading
        assert types[1] == 22  # divider
        assert types[2] == 14  # code
        assert types[3] == "_table"
        assert types[4] == 12  # bullet
        assert types[5] == 13  # ordered
        assert types[6] == "_quote"  # blockquote → quote container
        assert types[7] == 2   # plain text

    def test_real_technical_doc(self):
        """Real-world-like Chinese technical doc."""
        md = """# 架构说明

## 核心组件

- **main.py** — 入口点
- **bot.py** — WebSocket Bot

## 部署

```bash
./scripts/promote.sh
```

| 组件 | 版本 |
|------|------|
| Python | 3.13 |

---

详见 [文档](https://feishu.cn/docx/plan123)。
"""
        blocks = text_to_blocks(md)
        types = [b.get("block_type") for b in blocks]
        assert 3 in types   # H1
        assert 4 in types   # H2
        assert 12 in types  # bullet
        assert 14 in types  # code
        assert 22 in types  # divider
        tables = [b for b in blocks if "_table" in b]
        assert len(tables) == 1
        assert tables[0]["_table"][0] == ["组件", "版本"]


# ── split_table_rows ──


class TestSplitTableRows:
    def test_small_table_single_chunk(self):
        rows = [["A", "B"], ["1", "2"], ["3", "4"]]
        chunks = split_table_rows(rows)
        assert len(chunks) == 1
        assert chunks[0] == rows

    def test_header_repeated_in_each_chunk(self):
        """Each chunk must include the header row."""
        header = ["Col1", "Col2"]
        rows = [header] + [[str(i), str(i)] for i in range(TABLE_MAX_ROWS + 10)]
        chunks = split_table_rows(rows)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert chunk[0] == header


# ── Code language map (DocxCodeLanguage enum) ──


class TestCodeLangMap:
    """Verify key language IDs match the official Feishu DocxCodeLanguage enum."""

    def test_python(self):
        assert _CODE_LANG_MAP["python"] == 49
        assert _CODE_LANG_MAP["py"] == 49

    def test_javascript(self):
        assert _CODE_LANG_MAP["javascript"] == 30
        assert _CODE_LANG_MAP["js"] == 30

    def test_typescript(self):
        assert _CODE_LANG_MAP["typescript"] == 63
        assert _CODE_LANG_MAP["ts"] == 63

    def test_go(self):
        assert _CODE_LANG_MAP["go"] == 22

    def test_java(self):
        assert _CODE_LANG_MAP["java"] == 29

    def test_json(self):
        assert _CODE_LANG_MAP["json"] == 28

    def test_bash_shell(self):
        assert _CODE_LANG_MAP["bash"] == 7
        assert _CODE_LANG_MAP["shell"] == 60

    def test_sql(self):
        assert _CODE_LANG_MAP["sql"] == 56

    def test_yaml(self):
        assert _CODE_LANG_MAP["yaml"] == 67
        assert _CODE_LANG_MAP["yml"] == 67

    def test_rust(self):
        assert _CODE_LANG_MAP["rust"] == 53

    def test_r_not_python(self):
        """R=50 must not collide with Python=49."""
        assert _CODE_LANG_MAP["r"] == 50
        assert _CODE_LANG_MAP["python"] != _CODE_LANG_MAP["r"]


# ── Strikethrough ──


class TestStrikethrough:
    def test_basic_strikethrough(self):
        elements = _parse_inline("这是~~删除线~~文本")
        strike = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("strikethrough")),
            None,
        )
        assert strike is not None
        assert strike["text_run"]["content"] == "删除线"

    def test_strikethrough_in_block(self):
        blocks = text_to_blocks("~~已完成~~的任务")
        elements = blocks[0]["text"]["elements"]
        strike = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("strikethrough")),
            None,
        )
        assert strike is not None

    def test_bold_and_strikethrough_together(self):
        elements = _parse_inline("**粗体** and ~~删除~~")
        bold = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("bold")),
            None,
        )
        strike = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("strikethrough")),
            None,
        )
        assert bold is not None
        assert strike is not None


# ── HTML tag stripping ──


class TestHTMLTagStripping:
    def test_font_color_stripped(self):
        result = _strip_html_tags("<font color='red'>红色文本</font>")
        assert result == "红色文本"

    def test_font_color_double_quotes(self):
        result = _strip_html_tags('<font color="blue">蓝色</font>')
        assert result == "蓝色"

    def test_text_tag_stripped(self):
        result = _strip_html_tags("<text_tag color='blue'>标签</text_tag>")
        assert result == "标签"

    def test_at_mention_stripped(self):
        result = _strip_html_tags("<at id=ou_abc123></at>")
        assert result == ""

    def test_at_all_stripped(self):
        result = _strip_html_tags("<at id=all></at>")
        assert result == ""

    def test_mixed_html_and_markdown(self):
        text = "这是<font color='red'>红色</font>和**粗体**"
        elements = _parse_inline(text)
        # font tag stripped, bold parsed
        texts = [e["text_run"]["content"] for e in elements]
        assert "红色" in "".join(texts)
        bold = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("bold")),
            None,
        )
        assert bold is not None

    def test_html_in_doc_block(self):
        blocks = text_to_blocks("<font color='green'>成功</font>消息")
        content = "".join(
            e["text_run"]["content"] for e in blocks[0]["text"]["elements"]
        )
        assert "<font" not in content
        assert "成功" in content


# ── Card header stripping ──


class TestCardHeaderStripping:
    def test_card_header_removed(self):
        md = "{{card:header=部署完成,color=green}}\n服务已更新。"
        blocks = text_to_blocks(md)
        for b in blocks:
            if "text" in b:
                content = b["text"]["elements"][0]["text_run"]["content"]
                assert "{{card:" not in content

    def test_card_header_content_preserved(self):
        md = "{{card:header=标题,color=red}}\n正文内容"
        blocks = text_to_blocks(md)
        assert len(blocks) == 1
        assert blocks[0]["text"]["elements"][0]["text_run"]["content"] == "正文内容"

    def test_no_card_header_unchanged(self):
        md = "普通文本"
        blocks = text_to_blocks(md)
        assert blocks[0]["text"]["elements"][0]["text_run"]["content"] == "普通文本"


# ── Blockquote inline formatting ──


class TestBlockquoteInline:
    def _get_quote_elements(self, md: str, child_idx: int = 0) -> list[dict]:
        blocks = text_to_blocks(md)
        assert "_quote" in blocks[0]
        return blocks[0]["_quote"][child_idx]["text"]["elements"]

    def test_quote_with_bold(self):
        elements = self._get_quote_elements("> **重点**内容")
        bold = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("bold")),
            None,
        )
        assert bold is not None
        assert bold["text_run"]["content"] == "重点"

    def test_quote_with_code(self):
        elements = self._get_quote_elements("> 使用 `cmd` 命令")
        code = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("inline_code")),
            None,
        )
        assert code is not None

    def test_quote_with_link(self):
        elements = self._get_quote_elements("> 参见 [文档](https://example.com)")
        link = next(
            (e for e in elements if e["text_run"].get("text_element_style", {}).get("link")),
            None,
        )
        assert link is not None

    def test_empty_quote_unchanged(self):
        blocks = text_to_blocks(">")
        assert "_quote" in blocks[0]
        assert blocks[0]["_quote"][0]["block_type"] == 2
