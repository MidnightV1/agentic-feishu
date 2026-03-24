"""Feishu URL parsing utilities."""

import re
from typing import Any

# Matches various Feishu URL formats — order matters: more specific patterns first
_FEISHU_URL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # docx: https://xxx.feishu.cn/docx/{doc_id}
    (re.compile(r'feishu\.cn/docx/([A-Za-z0-9]+)'), "docx"),
    # sheet: https://xxx.feishu.cn/sheets/{token}
    (re.compile(r'feishu\.cn/sheets/([A-Za-z0-9]+)'), "sheet"),
    # bitable: https://xxx.feishu.cn/base/{token}
    (re.compile(r'feishu\.cn/base/([A-Za-z0-9]+)'), "bitable"),
    # wiki: https://xxx.feishu.cn/wiki/{token}
    (re.compile(r'feishu\.cn/wiki/([A-Za-z0-9]+)'), "wiki"),
    # drive folder: https://xxx.feishu.cn/drive/folder/{token}
    (re.compile(r'feishu\.cn/drive/folder/([A-Za-z0-9]+)'), "folder"),
    # file: https://xxx.feishu.cn/file/{token}
    (re.compile(r'feishu\.cn/file/([A-Za-z0-9]+)'), "file"),
]


def parse_feishu_url(url_or_id: str) -> tuple[str, str]:
    """Extract (token, type) from a Feishu URL or raw ID.

    Returns:
        (token, doc_type) where doc_type is one of:
            docx, sheet, bitable, wiki, folder, file, unknown
        If input is not a URL, returns (url_or_id, "unknown").
    """
    if not isinstance(url_or_id, str):
        return str(url_or_id), "unknown"

    stripped = url_or_id.strip()

    # Only attempt URL parsing if it looks like a URL
    if "feishu.cn" in stripped or "larkoffice.com" in stripped:
        for pattern, doc_type in _FEISHU_URL_PATTERNS:
            m = pattern.search(stripped)
            if m:
                return m.group(1), doc_type

    # Not a recognisable URL — return as-is
    return stripped, "unknown"


def extract_token(url_or_id: str) -> str:
    """Extract just the token from a URL, or return as-is if already a token."""
    token, _ = parse_feishu_url(url_or_id)
    return token


_BITABLE_TABLE_ID_RE = re.compile(r'[?&]table=(tbl[A-Za-z0-9]+)')


def extract_bitable_table_id(url_or_id: str) -> str:
    """Extract table_id from bitable URL query param, or return as-is."""
    if not isinstance(url_or_id, str):
        return str(url_or_id)
    m = _BITABLE_TABLE_ID_RE.search(url_or_id)
    return m.group(1) if m else url_or_id.strip()
