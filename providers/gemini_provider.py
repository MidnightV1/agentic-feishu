"""Google Gemini provider using the google-genai SDK."""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from google import genai
from google.genai import types

from core.types import Message, ToolCall, Usage
from providers.base import BaseProvider

logger = logging.getLogger(__name__)


class GeminiProvider(BaseProvider):
    """Gemini provider via the google-genai SDK.

    Converts between unified Message format and Gemini's
    Content/Part-based API.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
    ):
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._last_usage = Usage()

    @property
    def name(self) -> str:
        return "gemini"

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
        system_instruction, contents = self._convert_messages(messages)
        gemini_tools = self._convert_tools(tools) if tools else None

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=gemini_tools,
            temperature=kwargs.pop("temperature", None),
            max_output_tokens=kwargs.pop("max_tokens", None),
        )

        model = kwargs.pop("model", self._model)

        if stream:
            return self._stream(model, contents, config)
        return await self._complete(model, contents, config)

    async def count_tokens(self, messages: list[Message]) -> int:
        system_instruction, contents = self._convert_messages(messages)
        result = await self._client.aio.models.count_tokens(
            model=self._model,
            contents=contents,
        )
        return result.total_tokens

    # ------------------------------------------------------------------
    # Non-streaming
    # ------------------------------------------------------------------

    async def _complete(
        self,
        model: str,
        contents: list[types.Content],
        config: types.GenerateContentConfig,
    ) -> Message:
        response = await self._client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        if response.usage_metadata:
            self._last_usage = Usage(
                input_tokens=response.usage_metadata.prompt_token_count or 0,
                output_tokens=response.usage_metadata.candidates_token_count or 0,
                cached_tokens=response.usage_metadata.cached_content_token_count or 0,
            )

        return self._parse_response(response)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    async def _stream(
        self,
        model: str,
        contents: list[types.Content],
        config: types.GenerateContentConfig,
    ) -> AsyncIterator[str]:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        async for chunk in self._client.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=config,
        ):
            if not chunk.candidates:
                continue

            for part in chunk.candidates[0].content.parts:
                if part.text:
                    text_parts.append(part.text)
                    yield part.text
                elif part.function_call:
                    fc = part.function_call
                    tool_calls.append(ToolCall(
                        id=fc.id or f"call_{fc.name}",
                        name=fc.name,
                        arguments=dict(fc.args) if fc.args else {},
                    ))

            if chunk.usage_metadata:
                self._last_usage = Usage(
                    input_tokens=chunk.usage_metadata.prompt_token_count or 0,
                    output_tokens=chunk.usage_metadata.candidates_token_count or 0,
                    cached_tokens=chunk.usage_metadata.cached_content_token_count or 0,
                )

        self._last_message = Message(
            role="assistant",
            content="".join(text_parts),
            tool_calls=tool_calls or None,
        )

    # ------------------------------------------------------------------
    # Format conversion: unified -> Gemini
    # ------------------------------------------------------------------

    def _convert_messages(
        self, messages: list[Message]
    ) -> tuple[str | None, list[types.Content]]:
        """Convert unified messages to Gemini format.

        Returns (system_instruction, contents).
        """
        system_parts: list[str] = []
        contents: list[types.Content] = []

        for msg in messages:
            if msg.role == "system":
                text = msg.content if isinstance(msg.content, str) else msg.text
                system_parts.append(text or "")
            elif msg.role == "user":
                text = msg.content if isinstance(msg.content, str) else msg.text
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=text or "")],
                ))
            elif msg.role == "assistant":
                parts: list[types.Part] = []
                text = msg.content if isinstance(msg.content, str) else msg.text
                if text:
                    parts.append(types.Part.from_text(text=text))
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        parts.append(types.Part.from_function_call(
                            name=tc.name,
                            args=tc.arguments,
                        ))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
            elif msg.role == "tool":
                # Tool results go as user-role function responses
                text = msg.content if isinstance(msg.content, str) else msg.text
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(
                        name=msg.name or "tool",
                        response={"result": text or ""},
                    )],
                ))

        system_instruction = "\n\n".join(system_parts) if system_parts else None
        return system_instruction, contents

    def _convert_tools(self, tools: list[dict]) -> list[types.Tool]:
        """Convert OpenAI-style tool defs to Gemini FunctionDeclarations."""
        declarations = []
        for tool in tools:
            func = tool.get("function", tool)
            params = func.get("parameters", {})
            declarations.append(types.FunctionDeclaration(
                name=func["name"],
                description=func.get("description", ""),
                parameters=params if params.get("properties") else None,
            ))
        return [types.Tool(function_declarations=declarations)]

    # ------------------------------------------------------------------
    # Format conversion: Gemini -> unified
    # ------------------------------------------------------------------

    def _parse_response(self, response) -> Message:
        """Convert Gemini response to unified Message."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        if not response.candidates:
            return Message(role="assistant", content="")

        for part in response.candidates[0].content.parts:
            if part.text:
                text_parts.append(part.text)
            elif part.function_call:
                fc = part.function_call
                tool_calls.append(ToolCall(
                    id=fc.id or f"call_{fc.name}",
                    name=fc.name,
                    arguments=dict(fc.args) if fc.args else {},
                ))

        return Message(
            role="assistant",
            content="\n".join(text_parts) if text_parts else "",
            tool_calls=tool_calls or None,
        )
