"""OpenAI-compatible provider (OpenAI, DeepSeek, KIMI, Qwen)."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import openai

from core.types import Message, ToolCall, Usage
from providers.base import BaseProvider

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseProvider):
    """Provider for OpenAI-compatible APIs.

    Works with OpenAI, DeepSeek, Moonshot (KIMI), Qwen, and any
    service that implements the OpenAI chat completions API.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        max_tokens: int = 8192,
        name_override: str = "",
    ):
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._max_tokens = max_tokens
        self._name_override = name_override
        self._last_usage = Usage()

    @property
    def name(self) -> str:
        return self._name_override or "openai"

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
        api_messages = self._convert_messages(messages)
        api_tools = self._convert_tools(tools) if tools else openai.NOT_GIVEN

        params: dict[str, Any] = {
            "model": kwargs.pop("model", self._model),
            "messages": api_messages,
            "tools": api_tools,
            "max_tokens": kwargs.pop("max_tokens", self._max_tokens),
            "stream": stream,
        }
        if "temperature" in kwargs:
            params["temperature"] = kwargs.pop("temperature")
        params.update(kwargs)

        if stream:
            return self._stream(params)
        return await self._complete(params)

    async def count_tokens(self, messages: list[Message]) -> int:
        """Approximate token count using tiktoken.

        Falls back to a character-based estimate if tiktoken
        doesn't have the encoding for the model.
        """
        try:
            import tiktoken
            try:
                enc = tiktoken.encoding_for_model(self._model)
            except KeyError:
                enc = tiktoken.get_encoding("cl100k_base")

            total = 0
            for msg in messages:
                text = msg.content if isinstance(msg.content, str) else msg.text
                total += len(enc.encode(text or "")) + 4  # role overhead
            return total
        except ImportError:
            # Fallback: ~4 chars per token
            total = 0
            for msg in messages:
                text = msg.content if isinstance(msg.content, str) else msg.text
                total += len(text or "") // 4 + 4
            return total

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

    async def _complete(self, params: dict[str, Any]) -> Message:
        params["stream"] = False
        response = await self._client.chat.completions.create(**params)
        choice = response.choices[0]

        if response.usage:
            self._last_usage = Usage(
                input_tokens=response.usage.prompt_tokens or 0,
                output_tokens=response.usage.completion_tokens or 0,
                cached_tokens=getattr(response.usage, "prompt_tokens_details", None)
                and getattr(response.usage.prompt_tokens_details, "cached_tokens", 0)
                or 0,
            )

        return self._parse_choice(choice)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def _stream(self, params: dict[str, Any]) -> AsyncIterator[str]:
        params["stream"] = True
        stream = await self._client.chat.completions.create(**params)

        text_parts: list[str] = []
        # Accumulate tool call deltas: {index: {id, name, args_json}}
        tool_acc: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            if not chunk.choices:
                # Usage-only chunk (some providers send this at the end)
                if chunk.usage:
                    self._last_usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens or 0,
                        output_tokens=chunk.usage.completion_tokens or 0,
                    )
                continue

            delta = chunk.choices[0].delta
            if delta is None:
                continue

            # Text content
            if delta.content:
                text_parts.append(delta.content)
                yield delta.content

            # Tool call deltas
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_acc:
                        tool_acc[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "args_json": "",
                        }
                    acc = tool_acc[idx]
                    if tc_delta.id:
                        acc["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            acc["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            acc["args_json"] += tc_delta.function.arguments

        # Build final message
        tool_calls = [
            ToolCall(
                id=acc["id"],
                name=acc["name"],
                arguments=json.loads(acc["args_json"] or "{}"),
            )
            for acc in tool_acc.values()
        ] or None

        self._last_message = Message(
            role="assistant",
            content="".join(text_parts),
            tool_calls=tool_calls,
        )

    # ------------------------------------------------------------------
    # Format conversion: unified -> OpenAI
    # ------------------------------------------------------------------

    def _convert_messages(self, messages: list[Message]) -> list[dict]:
        result = []
        for msg in messages:
            if msg.role == "tool":
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content if isinstance(msg.content, str) else msg.text,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                m: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.content if isinstance(msg.content, str) else msg.text or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
                result.append(m)
            else:
                text = msg.content if isinstance(msg.content, str) else msg.text
                result.append({"role": msg.role, "content": text or ""})
        return result

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Normalize to OpenAI tool format."""
        result = []
        for tool in tools:
            if "type" in tool and tool["type"] == "function":
                result.append(tool)
            else:
                # Bare function definition
                result.append({
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                    },
                })
        return result

    # ------------------------------------------------------------------
    # Format conversion: OpenAI -> unified
    # ------------------------------------------------------------------

    def _parse_choice(self, choice) -> Message:
        msg = choice.message
        tool_calls = None
        if msg.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=json.loads(tc.function.arguments or "{}"),
                )
                for tc in msg.tool_calls
            ]
        return Message(
            role="assistant",
            content=msg.content or "",
            tool_calls=tool_calls,
        )


# ------------------------------------------------------------------
# Factory functions for common OpenAI-compatible providers
# ------------------------------------------------------------------


def deepseek_provider(api_key: str, model: str = "deepseek-chat", **kwargs) -> OpenAIProvider:
    """Create a DeepSeek provider."""
    return OpenAIProvider(
        api_key=api_key,
        model=model,
        base_url="https://api.deepseek.com",
        name_override="deepseek",
        **kwargs,
    )


def kimi_provider(api_key: str, model: str = "kimi-k2.5", **kwargs) -> OpenAIProvider:
    """Create a Moonshot (KIMI) provider."""
    return OpenAIProvider(
        api_key=api_key,
        model=model,
        base_url="https://api.moonshot.cn/v1",
        name_override="kimi",
        **kwargs,
    )


def qwen_provider(api_key: str, model: str = "qwen-plus", **kwargs) -> OpenAIProvider:
    """Create a Qwen (DashScope) provider."""
    return OpenAIProvider(
        api_key=api_key,
        model=model,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        name_override="qwen",
        **kwargs,
    )
