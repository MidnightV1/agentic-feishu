# -*- coding: utf-8 -*-
"""Markdown → Feishu document blocks conversion.

Converts markdown text into Feishu block API format:
- Headings (H1-H6) → heading blocks (block_type 3-8)
- Paragraphs → text blocks (block_type 2)
- Ordered/unordered lists → list blocks (block_type 12/13)
- Code blocks → code blocks (block_type 14)
- Tables → _table markers (handled by append logic)
- Dividers → divider blocks (block_type 22)
- Blockquotes → text blocks with ▎ prefix
"""

from __future__ import annotations

import html
import re
from typing import Any

# Heading block types: H1=3, H2=4, ... H6=8
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_CODE_FENCE_RE = re.compile(r"^```(\w*)")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_TABLE_SEP_RE = re.compile(r"^\|[-\s|:]+\|$")
_ORDERED_LIST_RE = re.compile(r"^(\d+)\.\s+(.+)$")
_UNORDERED_LIST_RE = re.compile(r"^[-*]\s+(.+)$")

# Indented list patterns (for nested list detection)
_INDENTED_UNORDERED_RE = re.compile(r"^( {2,})[-*]\s+(.+)$")
_INDENTED_ORDERED_RE = re.compile(r"^( {2,})(\d+)\.\s+(.+)$")
_DIVIDER_RE = re.compile(r"^(-{3,}|<hr>)\s*$", re.IGNORECASE)
_QUOTE_RE = re.compile(r"^>\s*(.*)$")

# Language mapping for Feishu code blocks
_CODE_LANG_MAP = {
    # Ref: DocxCodeLanguage enum (chyroc/lark type_docx.go)
    "": 1, "text": 1, "plaintext": 1,
    "bash": 7, "sh": 7, "shell": 60, "zsh": 7,
    "c": 10, "cpp": 9, "c++": 9, "csharp": 8, "c#": 8,
    "css": 12, "dart": 15, "dockerfile": 18,
    "go": 22, "groovy": 23, "html": 24, "http": 26,
    "java": 29, "javascript": 30, "js": 30,
    "json": 28, "kotlin": 32, "latex": 33, "lua": 36,
    "makefile": 38, "markdown": 39, "md": 39,
    "nginx": 40, "objc": 41, "objective-c": 41,
    "php": 43, "perl": 44, "powershell": 46,
    "python": 49, "py": 49, "r": 50, "ruby": 52, "rust": 53,
    "scss": 55, "sql": 56, "scala": 57, "swift": 61,
    "typescript": 63, "ts": 63, "xml": 66, "yaml": 67, "yml": 67,
}

# Table limits (Feishu docx API)
TABLE_MAX_ROWS = 100
TABLE_MAX_COLS = 9
TABLE_MAX_CELLS = 200


def _sanitize_doc_text(text: str) -> str:
    """Strip control chars (except newline/tab) to prevent API 400 errors."""
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)


# Combined pattern for inline formatting: bold, italic, strikethrough, code, links
_INLINE_RE = re.compile(
    r"\*\*(.+?)\*\*"              # group 1: bold
    r"|~~(.+?)~~"                 # group 2: strikethrough
    r"|\*(.+?)\*"                 # group 3: italic
    r"|`(.+?)`"                   # group 4: inline code
    r"|\[([^\]]+)\]\(([^)]+)\)"   # group 5,6: link
)

# HTML tags that appear in Feishu card markdown but not supported in doc blocks
_HTML_STRIP_RE = re.compile(
    r"<font\s+color=['\"]?\w+['\"]?>(.*?)</font>"    # <font color='red'>text</font>
    r"|<text_tag\s+color=['\"]?\w+['\"]?>(.*?)</text_tag>"  # <text_tag color='blue'>text</text_tag>
    r"|<at\s+id=['\"]?[\w]+['\"]?>\s*</at>"           # <at id=open_id></at>
)


def _strip_html_tags(text: str) -> str:
    """Strip Feishu card HTML tags, keeping inner text content."""
    def _replace(m: re.Match) -> str:
        # Return first non-None group (the inner text), or empty string for <at>
        return m.group(1) or m.group(2) or ""
    return _HTML_STRIP_RE.sub(_replace, text)


def _parse_inline(text: str) -> list[dict]:
    """Parse inline markdown (bold, italic, strikethrough, code, link) into Feishu text elements."""
    # Pre-process: strip HTML tags not supported in doc blocks, decode HTML entities
    text = _strip_html_tags(text)
    text = html.unescape(text)

    elements: list[dict] = []
    pos = 0

    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            elements.append({"text_run": {"content": text[pos:m.start()]}})

        if m.group(1):  # bold
            elements.append({"text_run": {
                "content": m.group(1),
                "text_element_style": {"bold": True},
            }})
        elif m.group(2):  # strikethrough
            elements.append({"text_run": {
                "content": m.group(2),
                "text_element_style": {"strikethrough": True},
            }})
        elif m.group(3):  # italic
            elements.append({"text_run": {
                "content": m.group(3),
                "text_element_style": {"italic": True},
            }})
        elif m.group(4):  # inline code
            elements.append({"text_run": {
                "content": m.group(4),
                "text_element_style": {"inline_code": True},
            }})
        elif m.group(5):  # link
            url = m.group(6)
            if url.startswith("http://") or url.startswith("https://"):
                elements.append({"text_run": {
                    "content": m.group(5),
                    "text_element_style": {"link": {"url": url}},
                }})
            else:
                elements.append({"text_run": {"content": m.group(5)}})
        pos = m.end()

    if pos < len(text):
        elements.append({"text_run": {"content": text[pos:]}})

    return elements or [{"text_run": {"content": text}}]


def _is_table_line(line: str) -> bool:
    """Check if a line is part of a markdown table."""
    stripped = line.strip()
    return bool(stripped) and stripped.startswith('|') and stripped.endswith('|')


def _parse_markdown_table(lines: list[str]) -> list[list[str]] | None:
    """Parse consecutive markdown table lines into a 2D list of cell texts."""
    data_rows = []
    for line in lines:
        if re.match(r'^\|[-\s|:]+\|$', line):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if cells:
            data_rows.append(cells)

    if not data_rows:
        return None

    # Normalize: pad short rows
    col_count = max(len(r) for r in data_rows)
    for row in data_rows:
        while len(row) < col_count:
            row.append("")

    return data_rows


def split_table_rows(rows: list[list[str]]) -> list[list[list[str]]]:
    """Split a table into chunks respecting Feishu table limits."""
    col_count = max(len(rows[0]), 1) if rows else 1
    max_total_rows = max(1, TABLE_MAX_CELLS // col_count)
    chunk_size = min(max_total_rows, TABLE_MAX_ROWS) - 1

    if len(rows) <= chunk_size + 1:
        return [rows]
    header = rows[0]
    data = rows[1:]
    return [
        [header] + data[i:i + chunk_size]
        for i in range(0, len(data), chunk_size)
    ]



def _parse_list_line(line: str) -> tuple[int, int, str] | None:
    """Parse a list line, returning (indent_level, block_type, text) or None.

    indent_level: 0 for top-level, 1 for 2-space indent, 2 for 4-space, etc.
    block_type: 12 (bullet) or 13 (ordered).
    """
    # Try indented unordered
    m = _INDENTED_UNORDERED_RE.match(line)
    if m:
        level = len(m.group(1)) // 2
        return level, 12, m.group(2)
    # Try indented ordered
    m = _INDENTED_ORDERED_RE.match(line)
    if m:
        level = len(m.group(1)) // 2
        return level, 13, m.group(3)
    # Try top-level unordered
    stripped = line.strip()
    m = _UNORDERED_LIST_RE.match(stripped)
    if m:
        return 0, 12, m.group(1)
    # Try top-level ordered
    m = _ORDERED_LIST_RE.match(stripped)
    if m:
        return 0, 13, m.group(2)
    return None


def _collect_nested_list(lines: list[str], start: int) -> list[dict] | None:
    """Collect consecutive list lines starting at `start`.

    Returns None if all items are level 0 (no nesting detected).
    Returns a flat list of {"type", "depth", "elements"} dicts — depth is
    preserved for arbitrary nesting levels.  The descendant API consumer
    (`_create_nested_list`) rebuilds parent-child relationships via a stack.
    """
    raw: list[tuple[int, int, str]] = []  # (level, block_type, text)
    j = start
    while j < len(lines):
        line = lines[j].rstrip()
        if not line:
            break
        parsed = _parse_list_line(line)
        if parsed is None:
            break
        raw.append(parsed)
        j += 1

    if not raw:
        return None

    # No nesting → caller handles as flat blocks
    if not any(level > 0 for level, _, _ in raw):
        return None

    # Flat list with depth — supports unlimited nesting
    return [
        {
            "type": "bullet" if btype == 12 else "ordered",
            "depth": level,
            "elements": _parse_inline(text),
        }
        for level, btype, text in raw
    ]


def text_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """Convert markdown text to Feishu document blocks.

    Returns a list of block dicts. Tables are returned as special
    {"_table": [[row1], [row2], ...]} markers for the append function
    to handle via the table creation API.
    """
    markdown = markdown.replace("\\n", "\n")
    markdown = _sanitize_doc_text(markdown)
    # Strip card header syntax (chat-only, not for docs)
    markdown = re.sub(r"^\{\{card:.*?\}\}\s*\n?", "", markdown)
    lines = markdown.split("\n")
    blocks: list[dict] = []
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()

        # Skip empty lines
        if not line:
            i += 1
            continue

        # Fenced code block: ```lang ... ```
        cm = _CODE_FENCE_RE.match(line.strip())
        if cm:
            lang = cm.group(1) or ""
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not re.match(r'^```\s*$', lines[i].rstrip()):
                code_lines.append(lines[i].rstrip('\r'))
                i += 1
            if i < len(lines):  # skip closing ```
                i += 1
            code_content = "\n".join(code_lines)
            if not code_content:
                code_content = " "  # Feishu rejects empty code blocks
            blocks.append({
                "block_type": 14,
                "code": {
                    "style": {
                        "language": _CODE_LANG_MAP.get(lang.lower(), 1),
                    },
                    "elements": [{"text_run": {"content": code_content}}],
                },
            })
            continue

        # Collect consecutive table lines
        if _is_table_line(line):
            table_lines = []
            while i < len(lines) and _is_table_line(lines[i].rstrip()):
                table_lines.append(lines[i].rstrip())
                i += 1
            table_data = _parse_markdown_table(table_lines)
            if table_data:
                blocks.append({"_table": table_data})
            else:
                for tl in table_lines:
                    blocks.append({
                        "block_type": 2,
                        "text": {"elements": [{"text_run": {"content": tl}}]},
                    })
            continue

        # Divider
        if _DIVIDER_RE.match(line.strip()):
            blocks.append({"block_type": 22, "divider": {}})
            i += 1
            continue

        # Heading
        hm = _HEADING_RE.match(line.strip())
        if hm:
            level = len(hm.group(1))
            block_type = 2 + level  # H1=3, H2=4, ...
            blocks.append({
                "block_type": block_type,
                f"heading{level}": {
                    "elements": _parse_inline(hm.group(2)),
                },
            })
            i += 1
            continue

        # Blockquote: collect consecutive > lines into a single ▎-prefixed block
        qm = _QUOTE_RE.match(line)
        if qm:
            quote_lines: list[str] = []
            while i < len(lines):
                qm2 = _QUOTE_RE.match(lines[i].rstrip())
                if not qm2:
                    break
                quote_lines.append(qm2.group(1) or "")
                i += 1
            merged = "\n".join(quote_lines)
            elements = [{"text_run": {"content": "▎ "}}]
            if merged:
                elements.extend(_parse_inline(merged))
            blocks.append({
                "block_type": 2,
                "text": {"elements": elements},
            })
            continue

        # Unordered list: - item or * item (with nested list support)
        um = _UNORDERED_LIST_RE.match(line.strip())
        if um:
            nested_items = _collect_nested_list(lines, i)
            if nested_items is not None:
                blocks.append({"_nested_list": nested_items})
                i += len(nested_items)
            else:
                blocks.append({
                    "block_type": 12,
                    "bullet": {
                        "elements": _parse_inline(um.group(1)),
                    },
                })
                i += 1
            continue

        # Ordered list: 1. item (with nested list support)
        om = _ORDERED_LIST_RE.match(line.strip())
        if om:
            nested_items = _collect_nested_list(lines, i)
            if nested_items is not None:
                blocks.append({"_nested_list": nested_items})
                i += len(nested_items)
            else:
                blocks.append({
                    "block_type": 13,
                    "ordered": {
                        "elements": _parse_inline(om.group(2)),
                    },
                })
                i += 1
            continue

        # Regular text block with inline formatting
        blocks.append({
            "block_type": 2,
            "text": {
                "elements": _parse_inline(line),
            },
        })
        i += 1

    return blocks
