#!/usr/bin/env python3
"""Cross-project comparison: af blocks.py vs hub utils.py text_to_blocks.

Runs the same markdown inputs through both implementations,
normalizes known structural differences, and reports divergences.
"""

import json
import sys
from pathlib import Path

# Add both project roots to sys.path
AF_ROOT = Path(__file__).resolve().parent.parent
HUB_ROOT = Path("/Users/john/Agent Space/claude-hub")

sys.path.insert(0, str(AF_ROOT))

# Import af's text_to_blocks
from platforms.feishu.blocks import text_to_blocks as af_t2b

# Import hub's text_to_blocks via importlib to avoid module name collision
import importlib.util
_hub_utils_path = HUB_ROOT / "agent" / "platforms" / "feishu" / "utils.py"
_spec = importlib.util.spec_from_file_location("hub_feishu_utils", _hub_utils_path)
_hub_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hub_mod)
hub_t2b = _hub_mod.text_to_blocks


# --- Normalization ---

def normalize_blocks(blocks: list) -> list:
    """Both projects now use the same flat+depth format — no normalization needed."""
    return blocks


# --- Test cases ---

TEST_CASES = {
    "heading_h1": "# Hello World",
    "heading_h2": "## Section Title",
    "heading_h6": "###### Deep Heading",
    "bold": "This is **bold** text",
    "italic": "This is *italic* text",
    "strikethrough": "This is ~~deleted~~ text",
    "inline_code": "Use `print()` here",
    "link": "Visit [Google](https://google.com)",
    "combined_inline": "**bold** and *italic* and `code`",
    "code_block_python": "```python\nprint('hello')\n```",
    "code_block_js": "```javascript\nconsole.log('hi')\n```",
    "code_block_empty_lang": "```\nsome code\n```",
    "code_block_empty_body": "```python\n```",
    "bullet_flat": "- Item A\n- Item B\n- Item C",
    "ordered_flat": "1. First\n2. Second\n3. Third",
    "bullet_nested_2level": "- Parent\n  - Child A\n  - Child B",
    "bullet_nested_3level": "- L0\n  - L1\n    - L2",
    "blockquote_simple": "> This is a quote",
    "blockquote_with_bold": "> This is **bold** in quote",
    "blockquote_empty": ">",
    "divider": "---",
    "table_simple": "| Name | Age |\n|------|-----|\n| Alice | 30 |",
    "table_single_col": "| Header |\n|--------|\n| Data |",
    "html_font_tag": '<font color="red">Important</font> text',
    "html_text_tag": '<text_tag color="blue">Label</text_tag> here',
    "card_header": "{{card:header=Title,color=green}}\nContent here",
    "mixed_content": "# Title\n\nSome **bold** text\n\n```python\nx = 1\n```\n\n- Item 1\n- Item 2\n\n> Quote here\n\n---",
    "plain_text": "Just regular text",
    "text_with_special_chars": "Hello <world> & 'friends'",
}


def compare(name: str, markdown: str) -> dict | None:
    """Compare outputs. Returns diff dict if different, None if same."""
    af_out = normalize_blocks(af_t2b(markdown))
    hub_out = normalize_blocks(hub_t2b(markdown))

    if af_out == hub_out:
        return None

    return {
        "name": name,
        "markdown": markdown[:80],
        "af_blocks": len(af_out),
        "hub_blocks": len(hub_out),
        "af": af_out,
        "hub": hub_out,
    }


def main():
    print(f"Running {len(TEST_CASES)} comparison tests...\n")

    passed = 0
    failed = 0
    diffs = []

    for name, md in TEST_CASES.items():
        result = compare(name, md)
        if result is None:
            print(f"  ✓ {name}")
            passed += 1
        else:
            print(f"  ✗ {name}")
            diffs.append(result)
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")

    if diffs:
        print(f"\n{'='*60}")
        print("DIVERGENCES:\n")
        for d in diffs:
            print(f"--- {d['name']} ---")
            print(f"  Input: {d['markdown']}")
            print(f"  AF blocks: {d['af_blocks']}, Hub blocks: {d['hub_blocks']}")
            # Show first differing block
            for i, (a, h) in enumerate(zip(d["af"], d["hub"])):
                if a != h:
                    print(f"  Block {i} differs:")
                    print(f"    AF:  {json.dumps(a, ensure_ascii=False)[:200]}")
                    print(f"    Hub: {json.dumps(h, ensure_ascii=False)[:200]}")
                    break
            if len(d["af"]) != len(d["hub"]):
                print(f"  Block count mismatch: af={len(d['af'])}, hub={len(d['hub'])}")
            print()

    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
