# -*- coding: utf-8 -*-
"""Feishu outbound message dispatcher — card-based markdown delivery."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger("agentic.feishu.dispatcher")

MAX_CHUNK_LEN = 4000  # Feishu card markdown limit per element

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
    """Split markdown text into chunks respecting code blocks."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Find a good split point: prefer double newline, then single newline
        cut = text.rfind("\n\n", 0, max_len)
        if cut <= 0:
            cut = text.rfind("\n", 0, max_len)
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

        text, header, color = _parse_card_directive(text)
        chunks = _chunk_markdown(text)

        first_msg_id = None
        for i, chunk in enumerate(chunks):
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

    async def _send_card_raw(
        self, chat_id: str, card: dict, reply_message_id: str = ""
    ) -> str | None:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        content_json = json.dumps(card)

        try:
            if reply_message_id:
                # Use Reply API — separate endpoint: POST /messages/{id}/reply
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
                    .receive_id(chat_id)
                    .msg_type("interactive")
                    .content(content_json)
                    .build()
                )
                req = (
                    CreateMessageRequest.builder()
                    .receive_id_type("chat_id")
                    .request_body(body)
                    .build()
                )
                resp = await self._client.im.v1.message.acreate(req)

            if resp.success():
                return resp.data.message_id
            log.warning("Send card failed: code=%s msg=%s", resp.code, resp.msg)
            # 230011 = message withdrawn; fall back to non-reply send
            if resp.code == 230011 and reply_message_id:
                log.info("Reply target withdrawn, falling back to non-reply send")
                return await self._send_card_raw(chat_id, card)
            return None
        except Exception as e:
            log.exception("Send card error: %s", e)
            return None

    async def send_text(self, chat_id: str, text: str) -> str | None:
        """Send a plain text message (fallback)."""
        if not self._client:
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
