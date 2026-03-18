"""Abstract base provider for LLM integrations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator

from core.types import Message, Usage

logger = logging.getLogger(__name__)


class BaseProvider(ABC):
    """Abstract LLM provider.

    All providers convert between our unified Message format and
    the provider-specific wire format internally.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = False,
        **kwargs,
    ) -> Message | AsyncIterator[str]:
        """Send messages and get a response.

        Args:
            messages: Conversation history in unified format.
            tools: Tool definitions (OpenAI-style JSON Schema).
            stream: If True, return an AsyncIterator yielding text deltas.
            **kwargs: Provider-specific overrides (temperature, max_tokens, etc.).

        Returns:
            A Message (non-streaming) or AsyncIterator[str] (streaming).
        """
        ...

    @abstractmethod
    async def count_tokens(self, messages: list[Message]) -> int:
        """Count tokens for the given messages."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging."""
        ...

    @property
    def last_usage(self) -> Usage:
        """Token usage from the most recent chat() call.

        Providers should update this after each call.
        """
        return getattr(self, "_last_usage", Usage())
