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

import re
from typing import Any

# Heading block types: H1=3, H2=4, ... H6=8
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_CODE_FENCE_RE = re.compile(r"^```(\w*)")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_TABLE_SEP_RE = re.compile(r"^\|[-\s|:]+\|$")
_ORDERED_LIST_RE = re.compile(r"^(\d+)\.\s+(.+)$")
_UNORDERED_LIST_RE = re.compile(r"^[-*]\s+(.+)$")
_DIVIDER_RE = re.compile(r"^(-{3,}|<hr>)\s*$", re.IGNORECASE)
_QUOTE_RE = re.compile(r"^>\s*(.*)$")

# Language mapping for Feishu code blocks
_CODE_LANG_MAP = {
    "": 1, "text": 1, "plaintext": 1,
    "bash": 7, "sh": 7, "shell": 61, "zsh": 7,
    "c": 10, "cpp": 9, "c++": 9, "csharp": 8, "c#": 8,
    "css": 12, "dart": 15, "dockerfile": 18,
    "go": 23, "groovy": 24, "html": 25, "http": 27,
    "java": 30, "javascript": 31, "js": 31,
    "json": 29, "kotlin": 33, "latex": 34, "lua": 37,
    "makefile": 39, "markdown": 40, "md": 40,
    "nginx": 41, "objc": 42, "objective-c": 42,
    "php": 44, "perl": 45, "powershell": 47,
    "python": 50, "py": 50, "r": 51, "ruby": 53, "rust": 54,
    "scss": 56, "sql": 57, "scala": 58, "swift": 62,
    "typescript": 64, "ts": 64, "xml": 67, "yaml": 68, "yml": 68,
}

# Table limits (Feishu docx API)
TABLE_MAX_ROWS = 100
TABLE_MAX_COLS = 9
TABLE_MAX_CELLS = 200


def _sanitize_doc_text(text: str) -> str:
    """Strip control chars (except newline/tab) to prevent API 400 errors."""
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)


# Combined pattern for inline formatting: bold, italic, code, links
_INLINE_RE = re.compile(
    r"\*\*(.+?)\*\*"              # bold
    r"|\*(.+?)\*"                 # italic
    r"|`(.+?)`"                   # inline code
    r"|\[([^\]]+)\]\(([^)]+)\)"   # link
)


def _parse_inline(text: str) -> list[dict]:
    """Parse inline markdown (bold, italic, code, link) into Feishu text elements."""
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
        elif m.group(2):  # italic
            elements.append({"text_run": {
                "content": m.group(2),
                "text_element_style": {"italic": True},
            }})
        elif m.group(3):  # inline code
            elements.append({"text_run": {
                "content": m.group(3),
                "text_element_style": {"inline_code": True},
            }})
        elif m.group(4):  # link
            url = m.group(5)
            if url.startswith("http://") or url.startswith("https://"):
                elements.append({"text_run": {
                    "content": m.group(4),
                    "text_element_style": {"link": {"url": url}},
                }})
            else:
                elements.append({"text_run": {"content": m.group(4)}})
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


def text_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """Convert markdown text to Feishu document blocks.

    Returns a list of block dicts. Tables are returned as special
    {"_table": [[row1], [row2], ...]} markers for the append function
    to handle via the table creation API.
    """
    markdown = markdown.replace("\\n", "\n")
    markdown = _sanitize_doc_text(markdown)
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

        # Blockquote: > text → ▎prefix
        qm = _QUOTE_RE.match(line)
        if qm:
            content = qm.group(1) or ""
            elements = [{"text_run": {"content": f"▎{content}" if content else "▎"}}]
            blocks.append({
                "block_type": 2,
                "text": {"elements": elements},
            })
            i += 1
            continue

        # Unordered list: - item or * item
        um = _UNORDERED_LIST_RE.match(line.strip())
        if um:
            blocks.append({
                "block_type": 12,
                "bullet": {
                    "elements": _parse_inline(um.group(1)),
                },
            })
            i += 1
            continue

        # Ordered list: 1. item
        om = _ORDERED_LIST_RE.match(line.strip())
        if om:
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
