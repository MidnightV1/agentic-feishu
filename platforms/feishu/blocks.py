# -*- coding: utf-8 -*-
"""Markdown → Feishu document blocks conversion.

Converts markdown text into Feishu block API format:
- Headings (H1-H6) → heading blocks (block_type 3-8)
- Paragraphs → text blocks (block_type 2)
- Ordered/unordered lists → list blocks (block_type 12/13)
- Code blocks → code blocks (block_type 14)
- Tables → _table markers (handled by append logic)
- Dividers → divider blocks (block_type 22)
"""

from __future__ import annotations

import re
from typing import Any

# Heading block types: H1=3, H2=4, ... H6=8
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_CODE_FENCE_RE = re.compile(r"^```(\w*)$")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_TABLE_SEP_RE = re.compile(r"^\|[-\s|:]+\|$")
_ORDERED_LIST_RE = re.compile(r"^(\d+)\.\s+(.+)$")
_UNORDERED_LIST_RE = re.compile(r"^[-*]\s+(.+)$")
_DIVIDER_RE = re.compile(r"^(-{3,}|<hr>)\s*$", re.IGNORECASE)

# Bold/italic/code inline patterns
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*(.+?)\*")
_CODE_INLINE_RE = re.compile(r"`(.+?)`")


def _parse_inline(text: str) -> list[dict]:
    """Parse inline markdown (bold, italic, code) into Feishu text elements."""
    elements: list[dict] = []
    pos = 0

    # Combined pattern for inline formatting: bold, italic, code, links
    combined = re.compile(
        r"\*\*(.+?)\*\*"              # bold
        r"|\*(.+?)\*"                 # italic
        r"|`(.+?)`"                   # inline code
        r"|\[([^\]]+)\]\(([^)]+)\)"   # link
    )

    for m in combined.finditer(text):
        # Add plain text before this match
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

    # Remaining text
    if pos < len(text):
        elements.append({"text_run": {"content": text[pos:]}})

    return elements or [{"text_run": {"content": text}}]


def text_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """Convert markdown text to Feishu document blocks.

    Returns a list of block dicts. Tables are returned as special
    {"_table": [[row1], [row2], ...]} markers for the append function
    to handle via the table creation API.
    """
    lines = markdown.split("\n")
    blocks: list[dict] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Skip empty lines
        if not line.strip():
            i += 1
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

        # Code block
        cm = _CODE_FENCE_RE.match(line.strip())
        if cm:
            lang = cm.group(1) or ""
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            blocks.append({
                "block_type": 14,
                "code": {
                    "language": _map_language(lang),
                    "elements": [{"text_run": {"content": "\n".join(code_lines)}}],
                },
            })
            continue

        # Table
        if _TABLE_ROW_RE.match(line.strip()):
            table_rows: list[list[str]] = []
            while i < len(lines) and _TABLE_ROW_RE.match(lines[i].strip()):
                row_text = lines[i].strip()
                if _TABLE_SEP_RE.match(row_text):
                    i += 1
                    continue
                cells = [c.strip() for c in row_text.strip("|").split("|")]
                table_rows.append(cells)
                i += 1
            if table_rows:
                blocks.append({"_table": table_rows})
            continue

        # Ordered list
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

        # Unordered list
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

        # Regular text block
        blocks.append({
            "block_type": 2,
            "text": {
                "elements": _parse_inline(line),
            },
        })
        i += 1

    return blocks


def _map_language(lang: str) -> int:
    """Map language string to Feishu code block language enum."""
    lang_map = {
        "python": 49, "py": 49, "javascript": 28, "js": 28,
        "typescript": 62, "ts": 62, "java": 27, "go": 21,
        "rust": 53, "c": 7, "cpp": 9, "bash": 3, "sh": 3,
        "shell": 3, "sql": 56, "json": 29, "yaml": 69,
        "html": 24, "css": 10, "ruby": 52, "php": 46,
        "swift": 58, "kotlin": 32,
    }
    return lang_map.get(lang.lower(), 0)  # 0 = PlainText
