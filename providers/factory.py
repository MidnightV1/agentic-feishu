"""Provider factory — create provider instances from settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from providers.base import BaseProvider

if TYPE_CHECKING:
    from config.settings import Settings


def create_provider(settings: Settings, provider_name: str = "") -> BaseProvider:
    """Create a provider instance from settings.

    Args:
        settings: Application settings.
        provider_name: Provider name override. Uses settings.default_provider if empty.

    Returns:
        Configured BaseProvider instance.
    """
    name = provider_name or settings.default_provider
    cfg = getattr(settings, name, None)
    if cfg is None:
        raise ValueError(f"Unknown provider: {name}")

    if name == "anthropic":
        from providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=cfg.api_key,
            model=cfg.model,
        )

    if name == "gemini":
        from providers.gemini_provider import GeminiProvider

        return GeminiProvider(
            api_key=cfg.api_key,
            model=cfg.model,
        )

    # OpenAI-compatible providers
    from providers.openai_provider import OpenAIProvider

    return OpenAIProvider(
        api_key=cfg.api_key,
        model=cfg.model,
        base_url=cfg.base_url or "https://api.openai.com/v1",
        name_override=name,
    )
