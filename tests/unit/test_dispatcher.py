# -*- coding: utf-8 -*-
"""Unit tests for dispatcher: chunking, secret scanning, card building, send logic."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from platforms.feishu.dispatcher import (
    _contains_secret,
    _parse_card_directive,
    _build_card,
    _chunk_markdown,
    MAX_CHUNK_LEN,
    FeishuDispatcher,
)


# ── Secret scanning ──


class TestSecretScanning:
    def test_anthropic_key(self):
        text = "use this key: sk-ant-api03-abcdefghijklmnopqrst"
        assert _contains_secret(text) is not None

    def test_openai_key(self):
        text = "sk-abcdefghijklmnopqrstuvwxyz"
        assert _contains_secret(text) is not None

    def test_github_pat(self):
        text = "ghp_1234567890abcdef"
        assert _contains_secret(text) is not None

    def test_github_oauth(self):
        text = "gho_1234567890abcdef"
        assert _contains_secret(text) is not None

    def test_github_fine_grained(self):
        text = "github_pat_abcdefghijklmnopqrstuv"
        assert _contains_secret(text) is not None

    def test_slack_token(self):
        text = "xoxb-12345678-abcdefgh"
        assert _contains_secret(text) is not None

    def test_google_api_key(self):
        text = "AIzaSyA_0123456789abcdefghijklmnopqrs"
        assert _contains_secret(text) is not None

    def test_aws_access_key(self):
        text = "AKIAIOSFODNN7EXAMPLE"
        assert _contains_secret(text) is not None

    def test_private_key(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."
        assert _contains_secret(text) is not None

    def test_clean_text(self):
        text = "This is normal text with no secrets."
        assert _contains_secret(text) is None

    def test_partial_match_not_triggered(self):
        """Short strings that look like prefixes but aren't full keys."""
        assert _contains_secret("sk-short") is None
        assert _contains_secret("ghp_abc") is None


# ── Card directive parsing ──


class TestCardDirective:
    def test_full_directive(self):
        text = "{{card:header=部署完成,color=green}}\n内容"
        remaining, header, color = _parse_card_directive(text)
        assert header == "部署完成"
        assert color == "green"
        assert remaining == "内容"

    def test_header_only(self):
        text = "{{card:header=测试}}\n正文"
        remaining, header, color = _parse_card_directive(text)
        assert header == "测试"
        assert color is None

    def test_no_directive(self):
        text = "普通消息"
        remaining, header, color = _parse_card_directive(text)
        assert remaining == "普通消息"
        assert header is None
        assert color is None

    def test_case_insensitive(self):
        text = "{{CARD:header=OK,color=blue}}\ntext"
        remaining, header, color = _parse_card_directive(text)
        assert header == "OK"
        assert color == "blue"


# ── Card building ──


class TestBuildCard:
    def test_basic_card(self):
        card = _build_card("hello")
        assert card["schema"] == "2.0"
        assert card["body"]["elements"][0]["content"] == "hello"
        assert "header" not in card

    def test_card_with_header(self):
        card = _build_card("body", header="标题", color="green")
        assert card["header"]["title"]["content"] == "标题"
        assert card["header"]["template"] == "green"

    def test_default_color(self):
        card = _build_card("body", header="H")
        assert card["header"]["template"] == "blue"

    def test_unknown_color_fallback(self):
        card = _build_card("body", header="H", color="nonexistent")
        assert card["header"]["template"] == "blue"


# ── Markdown chunking ──


class TestChunkMarkdown:
    def test_short_text_no_split(self):
        text = "short"
        assert _chunk_markdown(text) == ["short"]

    def test_exact_limit(self):
        text = "a" * MAX_CHUNK_LEN
        assert _chunk_markdown(text) == [text]

    def test_split_at_paragraph(self):
        """Prefers paragraph boundary."""
        part1 = "a" * 2000
        part2 = "b" * 2000
        text = part1 + "\n\n" + part2
        chunks = _chunk_markdown(text, max_len=2500)
        assert len(chunks) == 2
        assert chunks[0] == part1

    def test_split_at_newline(self):
        """Falls back to newline when no paragraph break."""
        part1 = "a" * 2000
        part2 = "b" * 2000
        text = part1 + "\n" + part2
        chunks = _chunk_markdown(text, max_len=2500)
        assert len(chunks) == 2

    def test_code_block_not_split(self):
        """Never splits inside a code block."""
        code = "```python\n" + "x = 1\n" * 400 + "```"
        before = "intro\n\n"
        after = "\n\nconclusion " + "z" * 1000
        text = before + code + after
        chunks = _chunk_markdown(text, max_len=2500)
        # Verify no chunk starts mid-code-block
        for chunk in chunks:
            fence_count = chunk.count("```")
            assert fence_count % 2 == 0, f"Unbalanced fences in chunk: {chunk[:100]}..."

    def test_hard_cut_as_last_resort(self):
        """If no break point, hard cut at max_len."""
        text = "a" * 8000  # no newlines at all
        chunks = _chunk_markdown(text, max_len=4000)
        assert len(chunks) == 2
        assert len(chunks[0]) == 4000

    def test_multiple_code_blocks(self):
        """Multiple code blocks split correctly when total exceeds limit."""
        block1 = "```\n" + "code1\n" * 200 + "```"
        block2 = "```\n" + "code2\n" * 200 + "```"
        text = block1 + "\n\n" + block2
        chunks = _chunk_markdown(text, max_len=1500)
        assert len(chunks) >= 2
        # Each chunk should have balanced fences
        for chunk in chunks:
            assert chunk.count("```") % 2 == 0

    def test_empty_after_strip(self):
        """Trailing newlines are stripped between chunks."""
        text = "a" * 3000 + "\n\n\n\n" + "b" * 3000
        chunks = _chunk_markdown(text, max_len=3500)
        assert len(chunks) == 2
        assert not chunks[1].startswith("\n")


# ── FeishuDispatcher async methods ──


@pytest.fixture
def dispatcher():
    d = FeishuDispatcher("test_id", "test_secret")
    d._client = MagicMock()
    return d


class TestSendCard:
    @pytest.mark.asyncio
    async def test_blocks_secrets(self, dispatcher):
        """Messages containing secrets are blocked (returns None)."""
        result = await dispatcher.send_card("chat1", "key: sk-ant-api03-abcdefghijklmnopqrst")
        assert result is None

    @pytest.mark.asyncio
    async def test_not_started(self):
        """Returns None if client not initialized."""
        d = FeishuDispatcher("a", "b")
        result = await d.send_card("chat1", "hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_single_chunk(self, dispatcher):
        """Short message sends as single card."""
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.message_id = "msg_001"
        dispatcher._client.im.v1.message.acreate = AsyncMock(return_value=mock_resp)

        result = await dispatcher.send_card("chat1", "hello")
        assert result == "msg_001"

    @pytest.mark.asyncio
    async def test_reply_path(self, dispatcher):
        """With reply_message_id, uses areply instead of acreate."""
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.message_id = "msg_002"
        dispatcher._client.im.v1.message.areply = AsyncMock(return_value=mock_resp)

        result = await dispatcher.send_card("chat1", "reply text", reply_message_id="parent_msg")
        assert result == "msg_002"
        dispatcher._client.im.v1.message.areply.assert_called_once()


class TestSendCardRaw230011:
    @pytest.mark.asyncio
    async def test_230011_fallback(self, dispatcher):
        """On 230011 error with reply, falls back to non-reply send."""
        # First call (reply) returns 230011
        withdrawn_resp = MagicMock()
        withdrawn_resp.success.return_value = False
        withdrawn_resp.code = 230011
        withdrawn_resp.msg = "message withdrawn"

        # Second call (non-reply) succeeds
        success_resp = MagicMock()
        success_resp.success.return_value = True
        success_resp.data.message_id = "msg_fallback"

        dispatcher._client.im.v1.message.areply = AsyncMock(return_value=withdrawn_resp)
        dispatcher._client.im.v1.message.acreate = AsyncMock(return_value=success_resp)

        card = _build_card("test")
        result = await dispatcher._send_card_raw("chat1", card, reply_message_id="withdrawn_msg")
        assert result == "msg_fallback"
        dispatcher._client.im.v1.message.acreate.assert_called_once()


class TestSendText:
    @pytest.mark.asyncio
    async def test_blocks_secrets(self, dispatcher):
        result = await dispatcher.send_text("chat1", "AKIAIOSFODNN7EXAMPLE")
        assert result is None

    @pytest.mark.asyncio
    async def test_not_started(self):
        d = FeishuDispatcher("a", "b")
        result = await d.send_text("chat1", "hello")
        assert result is None


class TestSendToUser:
    @pytest.mark.asyncio
    async def test_blocks_secrets(self, dispatcher):
        result = await dispatcher.send_to_user("ou_abc", "ghp_1234567890abcdef")
        assert result is None

    @pytest.mark.asyncio
    async def test_sends_with_open_id(self, dispatcher):
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        mock_resp.data.message_id = "msg_user"
        dispatcher._client.im.v1.message.acreate = AsyncMock(return_value=mock_resp)

        result = await dispatcher.send_to_user("ou_abc", "hello")
        assert result == "msg_user"


class TestButtonGroup:
    def test_bisected_layout(self):
        buttons = [
            {"text": "A", "value": {"k": "a"}},
            {"text": "B", "value": {"k": "b"}},
            {"text": "C", "value": {"k": "c"}},
        ]
        rows = FeishuDispatcher.build_button_group(buttons, "bisected")
        assert len(rows) == 2  # 2 buttons + 1 button
        assert rows[0]["tag"] == "column_set"
        assert len(rows[0]["columns"]) == 2

    def test_flow_layout(self):
        buttons = [{"text": "A"}, {"text": "B"}]
        rows = FeishuDispatcher.build_button_group(buttons, "flow")
        assert len(rows) == 1
        assert rows[0]["tag"] == "action"

    def test_trisected_layout(self):
        buttons = [{"text": str(i)} for i in range(6)]
        rows = FeishuDispatcher.build_button_group(buttons, "trisected")
        assert len(rows) == 2
        assert len(rows[0]["columns"]) == 3


class TestUpdateCardRetry:
    @pytest.mark.asyncio
    async def test_success_first_try(self, dispatcher):
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        dispatcher._client.im.v1.message.apatch = AsyncMock(return_value=mock_resp)
        assert await dispatcher.update_card("msg_1", "text") is True
        dispatcher._client.im.v1.message.apatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self, dispatcher):
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        dispatcher._client.im.v1.message.apatch = AsyncMock(
            side_effect=[ConnectionError("net"), mock_resp]
        )
        assert await dispatcher.update_card("msg_1", "text") is True
        assert dispatcher._client.im.v1.message.apatch.call_count == 2

    @pytest.mark.asyncio
    async def test_fails_after_3_attempts(self, dispatcher):
        dispatcher._client.im.v1.message.apatch = AsyncMock(
            side_effect=ConnectionError("net")
        )
        assert await dispatcher.update_card("msg_1", "text") is False
        assert dispatcher._client.im.v1.message.apatch.call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_programming_error(self, dispatcher):
        dispatcher._client.im.v1.message.apatch = AsyncMock(
            side_effect=TypeError("bad arg")
        )
        with pytest.raises(TypeError):
            await dispatcher.update_card("msg_1", "text")
        dispatcher._client.im.v1.message.apatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_error_no_retry(self, dispatcher):
        mock_resp = MagicMock()
        mock_resp.success.return_value = False
        mock_resp.code = 99999
        mock_resp.msg = "error"
        dispatcher._client.im.v1.message.apatch = AsyncMock(return_value=mock_resp)
        assert await dispatcher.update_card("msg_1", "text") is False
        dispatcher._client.im.v1.message.apatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_started(self):
        d = FeishuDispatcher("a", "b")
        assert await d.update_card("msg_1", "text") is False


class TestDeleteMessage:
    @pytest.mark.asyncio
    async def test_not_started(self):
        d = FeishuDispatcher("a", "b")
        assert await d.delete_message("msg_1") is False

    @pytest.mark.asyncio
    async def test_success(self, dispatcher):
        mock_resp = MagicMock()
        mock_resp.success.return_value = True
        dispatcher._client.im.v1.message.adelete = AsyncMock(return_value=mock_resp)
        assert await dispatcher.delete_message("msg_1") is True
