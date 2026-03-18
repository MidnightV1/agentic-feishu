"""Feishu platform adapter."""

from .adapter import FeishuAdapter
from .prompts import FEISHU_SYSTEM_PROMPT
from .tags import wrap_user_input, parse_output, ParsedOutput

__all__ = [
    "FeishuAdapter",
    "FEISHU_SYSTEM_PROMPT",
    "wrap_user_input",
    "parse_output",
    "ParsedOutput",
]
