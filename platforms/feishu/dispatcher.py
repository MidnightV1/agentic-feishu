# -*- coding: utf-8 -*-
"""Feishu outbound message dispatcher — card-based markdown delivery."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

log = logging.getLogger("agentic.feishu.dispatcher")

MAX_CHUNK_LEN = 4000  # Feishu card markdown limit per element

# ---------------------------------------------------------------------------
# Secret scanning — redact credentials before they reach Feishu
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = [
    re.compile(r'sk-ant-api03-[a-zA-Z0-9\-_]{20,}'),          # Anthropic
    re.compile(r'sk-[a-zA-Z0-9]{20,}'),                        # OpenAI
    re.compile(r'ghp_[a-zA-Z0-9]{10,}'),                       # GitHub PAT
    re.compile(r'gho_[a-zA-Z0-9]{10,}'),                       # GitHub OAuth
    re.compile(r'github_pat_[a-zA-Z0-9_]{22,}'),               # GitHub Fine-grained PAT
    re.compile(r'xox[baprs]-[a-zA-Z0-9\-]{10,}'),             # Slack
    re.compile(r'AIza[a-zA-Z0-9\-_]{30,}'),                   # Google API Key
    re.compile(r'AKIA[0-9A-Z]{16}'),                            # AWS Access Key
    re.compile(r'-----BEGIN [A-Z]+ PRIVATE KEY-----'),          # Private keys
]


def _contains_secret(text: str) -> str | None:
    """Check text for known secret patterns. Returns matched pattern snippet or None."""
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            return pat.pattern[:30]
    return None

# Card header directive: {{card:header=标题,color=blue}}
_CARD_DIRECTIVE_RE = re.compile(
    r"^\s*\{\{card:([^}]+)\}\}\s*\n?", re.IGNORECASE
)

CARD_COLORS = {
    "blue": "blue",
    "wathet": "wathet",
    "green": "green",
    "turquoise": "turquoise",
    "yellow": "yellow",
    "orange": "orange",
    "red": "red",
    "grey": "grey",
}


def _parse_card_directive(text: str) -> tuple[str, str | None, str | None]:
    """Extract {{card:header=...,color=...}} from text start."""
    m = _CARD_DIRECTIVE_RE.match(text)
    if not m:
        return text, None, None
    params_str = m.group(1)
    remaining = text[m.end() :]
    header = color = None
    for part in params_str.split(","):
        part = part.strip()
        if part.startswith("header="):
            header = part[7:].strip()
        elif part.startswith("color="):
            color = part[6:].strip()
    return remaining, header, color


def _build_card(markdown: str, header: str | None = None, color: str | None = None) -> dict:
    """Build a Feishu interactive card (JSON 2.0) from markdown text."""
    elements: list[dict] = []
    elements.append({"tag": "markdown", "content": markdown})

    card: dict[str, Any] = {
        "schema": "2.0",
        "body": {"elements": elements},
    }

    if header:
        card["header"] = {
            "title": {"tag": "plain_text", "content": header},
            "template": CARD_COLORS.get(color or "blue", "blue"),
        }

    return card


def _chunk_markdown(text: str, max_len: int = MAX_CHUNK_LEN) -> list[str]:
    """Split markdown into chunks, never splitting inside code blocks.

    Split preference: paragraph boundary (\\n\\n) > code block boundary
    (```\\n) > single newline (\\n) > hard cut.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        window = text[:max_len]

        # Count open/close ``` fences to know if a position is inside a code block.
        # We precompute fence positions in the window so we can check any candidate.
        fence_positions: list[int] = []
        idx = 0
        while True:
            pos = window.find("```", idx)
            if pos == -1:
                break
            fence_positions.append(pos)
            idx = pos + 3

        def _inside_code_block(pos: int) -> bool:
            """Return True if *pos* falls inside an unclosed code fence."""
            open_count = sum(1 for fp in fence_positions if fp < pos)
            return open_count % 2 == 1  # odd = inside

        # Try paragraph boundary (\n\n)
        cut = -1
        search_from = max_len
        while search_from > 0:
            candidate = window.rfind("\n\n", 0, search_from)
            if candidate <= 0:
                break
            if not _inside_code_block(candidate):
                cut = candidate
                break
            search_from = candidate  # keep looking earlier

        # Try code block boundary (```\n) — split after the closing fence line
        if cut <= 0:
            search_from = max_len
            while search_from > 0:
                candidate = window.rfind("```\n", 0, search_from)
                if candidate <= 0:
                    break
                end_of_fence = candidate + 4  # after "```\n"
                if not _inside_code_block(end_of_fence):
                    cut = end_of_fence
                    break
                search_from = candidate

        # Try single newline
        if cut <= 0:
            search_from = max_len
            while search_from > 0:
                candidate = window.rfind("\n", 0, search_from)
                if candidate <= 0:
                    break
                if not _inside_code_block(candidate):
                    cut = candidate
                    break
                search_from = candidate

        # Hard cut as last resort
        if cut <= 0:
            cut = max_len

        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


class FeishuDispatcher:
    """Sends messages to Feishu via card API."""

    def __init__(self, app_id: str, app_secret: str, domain: str = "https://open.feishu.cn"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.domain = domain
        self._client = None

    async def start(self) -> None:
        import lark_oapi as lark

        lark_domain = lark.FEISHU_DOMAIN
        if "larksuite" in self.domain:
            lark_domain = lark.LARK_DOMAIN

        self._client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .domain(lark_domain)
            .build()
        )
        log.info("Dispatcher started (app_id=%s…)", self.app_id[:8])

    async def stop(self) -> None:
        self._client = None

    async def send_card(
        self,
        chat_id: str,
        text: str,
        reply_message_id: str = "",
    ) -> str | None:
        """Send markdown text as a Feishu card. Returns message_id or None."""
        if not self._client:
            log.error("Dispatcher not started")
            return None

        secret = _contains_secret(text)
        if secret:
            log.warning("Blocked outbound message containing secret: %s", secret)
            return None
        text, header, color = _parse_card_directive(text)
        chunks = _chunk_markdown(text)

        first_msg_id = None
        for i, chunk in enumerate(chunks):
            if i > 0:
                await asyncio.sleep(0.5)
            card = _build_card(chunk, header if i == 0 else None, color)
            msg_id = await self._send_card_raw(
                chat_id, card, reply_message_id if i == 0 else ""
            )
            if i == 0:
                first_msg_id = msg_id

        return first_msg_id

    async def update_card(self, message_id: str, text: str) -> bool:
        """Update an existing card message (for streaming)."""
        if not self._client:
            return False

        text, header, color = _parse_card_directive(text)
        card = _build_card(text, header, color)

        import lark_oapi as lark
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

        body = PatchMessageRequestBody.builder().content(json.dumps(card)).build()
        req = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )

        try:
            resp = await self._client.im.v1.message.apatch(req)
            return resp.success()
        except Exception as e:
            log.warning("Card update failed: %s", e)
            return False

    _RETRY_MAX = 2  # max retries (total 3 attempts)

    async def _send_card_raw(
        self, receive_id: str, card: dict, reply_message_id: str = "",
        receive_id_type: str = "chat_id",
    ) -> str | None:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        content_json = json.dumps(card)
        last_exc: Exception | None = None

        for attempt in range(1 + self._RETRY_MAX):
            try:
                if reply_message_id:
                    from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
                    req = (
                        ReplyMessageRequest.builder()
                        .message_id(reply_message_id)
                        .request_body(
                            ReplyMessageRequestBody.builder()
                            .msg_type("interactive")
                            .content(content_json)
                            .build()
                        )
                        .build()
                    )
                    resp = await self._client.im.v1.message.areply(req)
                else:
                    body = (
                        CreateMessageRequestBody.builder()
                        .receive_id(receive_id)
                        .msg_type("interactive")
                        .content(content_json)
                        .build()
                    )
                    req = (
                        CreateMessageRequest.builder()
                        .receive_id_type(receive_id_type)
                        .request_body(body)
                        .build()
                    )
                    resp = await self._client.im.v1.message.acreate(req)

                # API responded — no retry for API-level errors
                if resp.success():
                    return resp.data.message_id
                log.warning("Send card failed: code=%s msg=%s", resp.code, resp.msg)
                # 230011 = message withdrawn; fall back to non-reply send
                if resp.code == 230011 and reply_message_id:
                    log.info("Reply target withdrawn, falling back to non-reply send")
                    return await self._send_card_raw(receive_id, card, receive_id_type=receive_id_type)
                return None

            except Exception as e:
                last_exc = e
                if attempt < self._RETRY_MAX:
                    delay = 2 ** (attempt + 1)  # exponential: 2s, 4s
                    log.warning(
                        "Send card network error (attempt %d/%d), retrying in %ds: %s",
                        attempt + 1, 1 + self._RETRY_MAX, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    log.exception("Send card error after %d attempts: %s", attempt + 1, e)

        return None

    async def send_text(self, chat_id: str, text: str) -> str | None:
        """Send a plain text message (fallback)."""
        if not self._client:
            return None

        secret = _contains_secret(text)
        if secret:
            log.warning("Blocked outbound text containing secret: %s", secret)
            return None

        import lark_oapi as lark
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )

        try:
            resp = await self._client.im.v1.message.acreate(req)
            if resp.success():
                return resp.data.message_id
            return None
        except Exception:
            log.exception("Send text error")
            return None

    async def send_to_user(self, open_id: str, text: str) -> str | None:
        """Send a card message directly to a user via open_id."""
        if not self._client:
            log.error("Dispatcher not started")
            return None

        secret = _contains_secret(text)
        if secret:
            log.warning("Blocked outbound message containing secret: %s", secret)
            return None
        text, header, color = _parse_card_directive(text)
        chunks = _chunk_markdown(text)

        first_msg_id = None
        for i, chunk in enumerate(chunks):
            if i > 0:
                await asyncio.sleep(0.5)
            card = _build_card(chunk, header if i == 0 else None, color)
            msg_id = await self._send_card_raw(
                open_id, card, receive_id_type="open_id",
            )
            if i == 0:
                first_msg_id = msg_id

        return first_msg_id

    # ── Interactive card builders ───────────────────────────────────

    @staticmethod
    def build_button_group(
        buttons: list[dict],
        layout: str = "bisected",
    ) -> list[dict]:
        """Build a button group for interactive cards.

        Each button dict: {text, value, type="default"|"primary"|"danger"}
        Layout: "bisected" (2 cols), "trisected" (3 cols), "flow" (single row)
        """
        elements: list[dict] = []
        for btn in buttons:
            elements.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": btn["text"]},
                "type": btn.get("type", "default"),
                "value": btn.get("value", {}),
            })

        if layout == "flow":
            return [{"tag": "action", "actions": elements}]

        # bisected / trisected → use column_set
        cols_per_row = 2 if layout == "bisected" else 3
        rows: list[dict] = []
        for i in range(0, len(elements), cols_per_row):
            chunk = elements[i : i + cols_per_row]
            columns = []
            for btn_el in chunk:
                columns.append({
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [btn_el],
                })
            rows.append({
                "tag": "column_set",
                "flex_mode": "stretch",
                "columns": columns,
            })
        return rows

    @staticmethod
    def build_interactive_card(
        elements: list[dict],
        header: str | None = None,
        color: str = "blue",
    ) -> dict:
        """Build a complete interactive card JSON 2.0 with custom elements."""
        card: dict = {
            "schema": "2.0",
            "body": {"elements": elements},
        }
        if header:
            card["header"] = {
                "title": {"tag": "plain_text", "content": header},
                "template": CARD_COLORS.get(color, "blue"),
            }
        return card

    async def send_card_raw(self, chat_id: str, card_json: dict) -> str | None:
        """Send a pre-built card JSON directly."""
        if not self._client:
            return None
        return await self._send_card_raw(chat_id, card_json)

    async def delete_message(self, message_id: str) -> bool:
        """Delete a message by ID."""
        if not self._client:
            return False

        from lark_oapi.api.im.v1 import DeleteMessageRequest

        req = DeleteMessageRequest.builder().message_id(message_id).build()
        try:
            resp = await self._client.im.v1.message.adelete(req)
            if resp.success():
                return True
            log.warning("Delete message failed: code=%s msg=%s", resp.code, resp.msg)
            return False
        except Exception:
            log.exception("Delete message error")
            return False
