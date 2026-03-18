"""Anthropic (Claude) provider using the native SDK."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import anthropic

from core.types import Message, ToolCall, Usage
from providers.base import BaseProvider

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseProvider):
    """Claude provider via the Anthropic Python SDK.

    Handles format conversion between unified Message and Anthropic's
    content-block based API. Supports tool use and prompt caching.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 8192,
    ):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens
        self._last_usage = Usage()

    @property
    def name(self) -> str:
        return "anthropic"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = False,
        **kwargs,
    ) -> Message | AsyncIterator[str]:
        system, api_messages = self._convert_messages(messages)
        api_tools = self._convert_tools(tools) if tools else anthropic.NOT_GIVEN

        params: dict[str, Any] = {
            "model": kwargs.pop("model", self._model),
            "max_tokens": kwargs.pop("max_tokens", self._max_tokens),
            "messages": api_messages,
            "tools": api_tools,
        }
        if system:
            params["system"] = system
        if "temperature" in kwargs:
            params["temperature"] = kwargs.pop("temperature")

        # Pass remaining kwargs through
        params.update(kwargs)

        if stream:
            return self._stream(params)
        return await self._complete(params)

    async def count_tokens(self, messages: list[Message]) -> int:
        system, api_messages = self._convert_messages(messages)
        params: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
        }
        if system:
            params["system"] = system
        result = await self._client.messages.count_tokens(**params)
        return result.input_tokens

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

    async def _complete(self, params: dict[str, Any]) -> Message:
        response = await self._client.messages.create(**params)

        self._last_usage = Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cached_tokens=getattr(response.usage, "cache_read_input_tokens", 0),
        )

        return self._parse_response(response)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def _stream(self, params: dict[str, Any]) -> AsyncIterator[str]:
        async with self._client.messages.stream(**params) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield delta.text

            # After stream ends, get the complete response
            final = await stream.get_final_message()
            self._last_usage = Usage(
                input_tokens=final.usage.input_tokens,
                output_tokens=final.usage.output_tokens,
                cached_tokens=getattr(final.usage, "cache_read_input_tokens", 0),
            )
            self._last_message = self._parse_response(final)

    # ------------------------------------------------------------------
    # Format conversion: unified -> Anthropic
    # ------------------------------------------------------------------

    def _convert_messages(
        self, messages: list[Message]
    ) -> tuple[list[dict] | None, list[dict]]:
        """Convert unified messages to Anthropic format.

        Returns (system_blocks, api_messages).
        Anthropic separates system from the message list.
        """
        system_blocks: list[dict] = []
        api_messages: list[dict] = []

        for msg in messages:
            if msg.role == "system":
                text = msg.content if isinstance(msg.content, str) else msg.text
                system_blocks.append({
                    "type": "text",
                    "text": text,
                    "cache_control": {"type": "ephemeral"},
                })
            elif msg.role == "user":
                api_messages.append({"role": "user", "content": self._to_anthropic_content(msg)})
            elif msg.role == "assistant":
                api_messages.append({"role": "assistant", "content": self._to_anthropic_assistant(msg)})
            elif msg.role == "tool":
                # Tool results in Anthropic go as user messages with tool_result blocks
                api_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content if isinstance(msg.content, str) else msg.text,
                    }],
                })

        return system_blocks or None, api_messages

    def _to_anthropic_content(self, msg: Message) -> str | list[dict]:
        """Convert user message content."""
        if isinstance(msg.content, str):
            return msg.content
        if isinstance(msg.content, list):
            blocks = []
            for b in msg.content:
                if b.type == "text" and b.text:
                    blocks.append({"type": "text", "text": b.text})
                # Extensible: image blocks etc.
            return blocks
        return msg.content or ""

    def _to_anthropic_assistant(self, msg: Message) -> list[dict]:
        """Convert assistant message to Anthropic content blocks."""
        blocks: list[dict] = []
        text = msg.content if isinstance(msg.content, str) else msg.text
        if text:
            blocks.append({"type": "text", "text": text})
        if msg.tool_calls:
            for tc in msg.tool_calls:
                blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                })
        return blocks

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert OpenAI-style tool defs to Anthropic format.

        OpenAI: {"type": "function", "function": {"name", "description", "parameters"}}
        Anthropic: {"name", "description", "input_schema"}
        """
        result = []
        for tool in tools:
            func = tool.get("function", tool)
            result.append({
                "name": func["name"],
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })
        return result

    # ------------------------------------------------------------------
    # Format conversion: Anthropic -> unified
    # ------------------------------------------------------------------

    def _parse_response(self, response) -> Message:
        """Convert Anthropic response to unified Message."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                ))

        return Message(
            role="assistant",
            content="\n".join(text_parts) if text_parts else "",
            tool_calls=tool_calls or None,
        )
