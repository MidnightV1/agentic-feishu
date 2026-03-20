"""OpenAI-compatible provider (OpenAI, DeepSeek, KIMI, Qwen)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import openai

from core.types import Message, ToolCall, Usage
from providers.base import BaseProvider
from providers.presets import get_model_info

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
        self._client = openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url,
            max_retries=3, timeout=120.0,
        )
        self._model = model
        self._max_tokens = max_tokens
        self._name_override = name_override
        self._last_usage = Usage()
        # Check if model is a reasoning/thinking model (e.g. deepseek-reasoner)
        info = get_model_info(name_override or "openai", model)
        self._is_reasoning = info.reasoning

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
            "stream": stream,
        }
        # Reasoning models (e.g. deepseek-reasoner) don't support temperature/max_tokens
        if self._is_reasoning:
            kwargs.pop("temperature", None)
            kwargs.pop("max_tokens", None)
            # Force streaming for reasoning models to avoid connection timeouts
            # during long thinking phases (non-streaming waits for full response)
            stream = True
            params["stream"] = True
        else:
            params["max_tokens"] = kwargs.pop("max_tokens", self._max_tokens)
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
        reasoning_parts: list[str] = []  # DeepSeek Reasoner thinking output
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

            # Reasoning content (DeepSeek Reasoner streams this before content)
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning_parts.append(rc)

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
            reasoning_content="".join(reasoning_parts) or None,
        )

    # ------------------------------------------------------------------
    # Format conversion: unified -> OpenAI
    # ------------------------------------------------------------------

    def _convert_messages(self, messages: list[Message]) -> list[dict]:
        """Convert unified messages to OpenAI API format.

        For reasoning models (e.g. deepseek-reasoner):
        - reasoning_content MUST be preserved in tool-call loops (assistant→tool→assistant)
        - reasoning_content CAN be omitted in non-tool-call history to save context
        We keep reasoning_content only on the last assistant message and any assistant
        message immediately followed by tool messages (i.e. active tool-call chains).
        """
        # Pre-scan: find assistant message indices that are in tool-call chains
        _in_tool_chain: set[int] = set()
        if self._is_reasoning:
            for i, msg in enumerate(messages):
                if msg.role == "assistant" and msg.tool_calls:
                    _in_tool_chain.add(i)
            # Also keep reasoning on the very last assistant message
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].role == "assistant":
                    _in_tool_chain.add(i)
                    break

        result = []
        for i, msg in enumerate(messages):
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
                # reasoning_content required in tool-call chains
                if msg.reasoning_content and i in _in_tool_chain:
                    m["reasoning_content"] = msg.reasoning_content
                result.append(m)
            elif msg.role == "assistant":
                text = msg.content if isinstance(msg.content, str) else msg.text
                m = {"role": "assistant", "content": text or ""}
                # Only keep reasoning_content where needed (last msg or tool-call chain)
                if msg.reasoning_content and i in _in_tool_chain:
                    m["reasoning_content"] = msg.reasoning_content
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
        # DeepSeek Reasoner returns reasoning_content (thinking output)
        reasoning_content = getattr(msg, "reasoning_content", None) or None
        return Message(
            role="assistant",
            content=msg.content or "",
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
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
