"""Main agent loop — messages in, tool calls out, repeat until done."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .types import Callbacks, Message, RunConfig, RunResult, ToolCall, ToolResult, Usage

log = logging.getLogger(__name__)

# P1-4: Retry config for transient errors
MAX_RETRIES = 3
RETRY_DELAYS = [2, 4, 8]  # seconds


class AgentLoop:
    """Drives a multi-turn conversation with tool use.

    Loop: messages -> provider.chat() -> check tool_calls -> execute tools
          -> append results -> repeat.
    Exit: no tool_calls, max_turns reached, or budget exceeded.
    """

    def __init__(self, provider: Any, tool_registry: Any, context_manager: Any = None):
        self._provider = provider
        self._tools = tool_registry
        self._context = context_manager

    async def run(
        self,
        prompt: str,
        config: RunConfig,
        system_prompt: str = "",
        messages: list[Message] | None = None,
        callbacks: Callbacks | None = None,
        provider: Any = None,
    ) -> RunResult:
        """Execute the agent loop until completion.

        Args:
            provider: Optional provider override (for per-session model switching).
                      If None, uses the default provider.
        """
        cb = callbacks or Callbacks()
        msgs = list(messages) if messages else []

        # Prepend system prompt
        if system_prompt:
            msgs.insert(0, Message(role="system", content=system_prompt))

        # Append user prompt
        msgs.append(Message(role="user", content=prompt))

        total_usage = Usage()
        total_cost = 0.0
        turn = 0

        while turn < config.max_turns:
            turn += 1
            log.debug("turn %d/%d", turn, config.max_turns)

            # Refresh tool schemas each turn (deferred tools may have expanded)
            tool_schemas = self._tools.get_tool_schemas()

            # P1-8: Disable tools for models that don't support them
            if not self._model_supports_tools(config.provider, config.model):
                tool_schemas = []

            # --- Call provider ---
            if cb.on_turn_start:
                await cb.on_turn_start(turn)
            assistant_msg, turn_usage = await self._call_provider(
                msgs, tool_schemas, config, cb, provider=provider,
            )
            msgs.append(assistant_msg)
            total_usage += turn_usage
            total_cost = self._estimate_cost(total_usage, config.provider, config.model)

            # --- Budget guard ---
            if total_cost >= config.max_budget_usd:
                log.warning("budget limit reached: $%.4f >= $%.4f", total_cost, config.max_budget_usd)
                return self._build_result(msgs, total_usage, total_cost, turn, "max_budget")

            # --- Tool execution ---
            if not assistant_msg.tool_calls:
                # No tool calls — model is done
                if cb.on_turn_end:
                    await cb.on_turn_end(turn, assistant_msg)
                return self._build_result(msgs, total_usage, total_cost, turn, "end_turn")

            results = await self._execute_tools(assistant_msg.tool_calls, cb)
            for tc, result in zip(assistant_msg.tool_calls, results):
                msgs.append(Message(
                    role="tool",
                    content=result.content,
                    tool_call_id=result.tool_call_id,
                    name=tc.name,  # tool function name, not call ID
                ))

            if cb.on_turn_end:
                await cb.on_turn_end(turn, assistant_msg)

        # Exhausted max_turns
        log.warning("max_turns reached: %d", config.max_turns)
        return self._build_result(msgs, total_usage, total_cost, turn, "max_turns")

    async def _call_provider(
        self,
        messages: list[Message],
        tool_schemas: list[dict],
        config: RunConfig,
        cb: Callbacks,
        provider: Any = None,
    ) -> tuple[Message, Usage]:
        """Call the LLM provider with transient error retry (P1-4)."""
        kwargs: dict[str, Any] = {}
        if config.temperature is not None:
            kwargs["temperature"] = config.temperature
        if config.model:
            kwargs["model"] = config.model

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                result = await self._do_provider_call(
                    messages, tool_schemas, config, cb, **kwargs,
                )
                # Check for empty result (transient provider hiccup)
                msg, usage = result
                if msg.content == "" and not msg.tool_calls:
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[attempt]
                        log.warning(
                            "Empty result, retry %d/%d in %ds",
                            attempt + 1, MAX_RETRIES, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                return result
            except Exception as e:
                if self._is_transient(e) and attempt < MAX_RETRIES:
                    delay = RETRY_DELAYS[attempt]
                    log.warning(
                        "Transient error %s, retry %d/%d in %ds",
                        e, attempt + 1, MAX_RETRIES, delay,
                    )
                    await asyncio.sleep(delay)
                    last_error = e
                    continue
                raise
        raise last_error or RuntimeError("Max retries exceeded")

    async def _do_provider_call(
        self,
        messages: list[Message],
        tool_schemas: list[dict],
        config: RunConfig,
        cb: Callbacks,
        **kwargs: Any,
    ) -> tuple[Message, Usage]:
        """Execute one provider call (streaming or non-streaming)."""
        if config.stream:
            return await self._call_streaming(messages, tool_schemas, cb, **kwargs)
        else:
            result = await self._provider.chat(
                messages, tools=tool_schemas or None, stream=False, **kwargs
            )
            # Provider may upgrade to streaming internally (e.g. reasoning models)
            if hasattr(result, "__aiter__"):
                return await self._consume_stream(result, cb)
            usage = getattr(self._provider, "_last_usage", Usage())
            return result, usage

    @staticmethod
    def _is_transient(e: Exception) -> bool:
        """Errors worth retrying."""
        error_str = str(e).lower()
        # Network/timeout errors
        if any(k in type(e).__name__.lower() for k in ("timeout", "connection", "network")):
            return True
        # HTTP 5xx or 429
        if hasattr(e, "status_code"):
            return e.status_code >= 500 or e.status_code == 429
        # Known transient patterns
        if any(k in error_str for k in ("timeout", "connection reset", "server error", "overloaded")):
            return True
        return False

    async def _consume_stream(self, stream, cb: Callbacks) -> tuple[Message, Usage]:
        """Consume a stream iterator returned by provider (internal upgrade to streaming)."""
        async for text_delta in stream:
            if cb.on_text and isinstance(text_delta, str):
                await cb.on_text(text_delta)
        msg = getattr(self._provider, "_last_message", None)
        usage = getattr(self._provider, "_last_usage", Usage())
        if msg is None:
            msg = Message(role="assistant", content="")
        return msg, usage

    async def _call_streaming(
        self,
        messages: list[Message],
        tool_schemas: list[dict],
        cb: Callbacks,
        **kwargs: Any,
    ) -> tuple[Message, Usage]:
        """Handle a streaming provider response.

        Providers yield text deltas (str) during streaming and store the
        final Message in ``_last_message`` and Usage in ``_last_usage``
        after the stream is exhausted.
        """
        stream = await self._provider.chat(
            messages, tools=tool_schemas or None, stream=True, **kwargs
        )

        return await self._consume_stream(stream, cb)

    async def _execute_tools(
        self, tool_calls: list[ToolCall], cb: Callbacks
    ) -> list[ToolResult]:
        """Execute tool calls — parallel-safe tools concurrently, others sequentially."""
        parallel: list[ToolCall] = []
        sequential: list[ToolCall] = []

        for tc in tool_calls:
            tool = self._tools.get_tool(tc.name)
            if tool is None:
                sequential.append(tc)  # will produce error result
                continue
            if getattr(tool, "parallel_safe", False):
                parallel.append(tc)
            else:
                sequential.append(tc)

        results: list[ToolResult] = []

        # Run parallel-safe tools concurrently
        if parallel:
            coros = [self._exec_one(tc, cb) for tc in parallel]
            results.extend(await asyncio.gather(*coros))

        # Run mutating tools sequentially
        for tc in sequential:
            results.append(await self._exec_one(tc, cb))

        return results

    async def _exec_one(self, tc: ToolCall, cb: Callbacks) -> ToolResult:
        """Execute a single tool call with callbacks."""
        if cb.on_tool_start:
            await cb.on_tool_start(tc.name, tc.arguments)

        tool = self._tools.get_tool(tc.name)
        if tool is None:
            result = ToolResult(
                tool_call_id=tc.id,
                content=f"Unknown tool: {tc.name}",
                is_error=True,
            )
        else:
            try:
                result = await self._tools.execute(tc.name, tc.arguments)
                result.tool_call_id = tc.id  # ensure ID is set
            except Exception as e:
                log.exception("tool %s raised", tc.name)
                result = ToolResult(
                    tool_call_id=tc.id,
                    content=f"Error executing {tc.name}: {e}",
                    is_error=True,
                )

        if cb.on_tool_end:
            await cb.on_tool_end(tc.name, result)

        return result

    @staticmethod
    def _model_supports_tools(provider: str, model: str) -> bool:
        """P1-8: Check if the model supports tool calling via presets."""
        try:
            from providers.presets import get_model_info
            info = get_model_info(provider, model)
            return info.tool_support if info else True
        except Exception:
            return True  # default: assume supported

    @staticmethod
    def _estimate_cost(usage: Usage, provider: str = "", model: str = "") -> float:
        """CTX-3: Per-model pricing from presets, fallback to Sonnet ballpark."""
        try:
            from providers.presets import get_model_info
            info = get_model_info(provider, model)
            if info and hasattr(info, "input_cost_per_m") and info.input_cost_per_m:
                return (
                    usage.input_tokens * info.input_cost_per_m / 1_000_000
                    + usage.output_tokens * info.output_cost_per_m / 1_000_000
                )
        except Exception:
            pass
        # Fallback: Sonnet 4 ballpark ($3/M input, $15/M output)
        input_cost = (usage.input_tokens - usage.cached_tokens) * 3.0 / 1_000_000
        cached_cost = usage.cached_tokens * 0.3 / 1_000_000
        output_cost = usage.output_tokens * 15.0 / 1_000_000
        return input_cost + cached_cost + output_cost

    @staticmethod
    def _build_result(
        messages: list[Message],
        usage: Usage,
        cost: float,
        turns: int,
        stop_reason: str,
    ) -> RunResult:
        # Extract final assistant text
        text = ""
        for msg in reversed(messages):
            if msg.role == "assistant":
                text = msg.text
                break
        return RunResult(
            text=text,
            messages=messages,
            usage=usage,
            cost_usd=cost,
            turn_count=turns,
            stop_reason=stop_reason,
        )
