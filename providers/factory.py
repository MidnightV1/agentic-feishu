# -*- coding: utf-8 -*-
"""Provider factory — create provider instances from settings + presets.

Resolution order for base_url and model:
  1. Explicit value in config.yaml / env var  (highest priority)
  2. Preset default from presets.py
  3. Hardcoded fallback

Users only need: provider name + API key.  Everything else is auto-resolved.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from providers.base import BaseProvider
from providers.presets import PRESETS, get_preset

if TYPE_CHECKING:
    from config.settings import Settings

log = logging.getLogger(__name__)


def create_provider(settings: Settings, provider_name: str = "") -> BaseProvider:
    """Create a provider instance, auto-resolving defaults from presets.

    Args:
        settings: Application settings.
        provider_name: Provider name override. Uses settings.default_provider if empty.

    Returns:
        Configured BaseProvider instance.
    """
    name = provider_name or settings.default_provider
    cfg = getattr(settings, name, None)
    if cfg is None:
        raise ValueError(
            f"Unknown provider: {name}. "
            f"Available: {', '.join(PRESETS.keys())}"
        )

    # Resolve from presets
    preset = get_preset(name)
    api_key = cfg.api_key
    model = cfg.model or (preset.default_model if preset else "")
    base_url = cfg.base_url or (preset.base_url if preset else "")

    if not api_key:
        log.warning("No API key configured for provider '%s'", name)

    # Native SDK providers
    if name == "anthropic" or (preset and preset.sdk == "anthropic"):
        from providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(api_key=api_key, model=model)

    if name == "gemini" or (preset and preset.sdk == "gemini"):
        from providers.gemini_provider import GeminiProvider

        return GeminiProvider(api_key=api_key, model=model)

    # OpenAI-compatible providers (all Chinese providers + OpenAI itself)
    from providers.openai_provider import OpenAIProvider

    if not base_url:
        base_url = "https://api.openai.com/v1"

    provider = OpenAIProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        name_override=name,
    )

    log.info(
        "Created provider: %s (model=%s, base_url=%s)",
        name, model, base_url,
    )
    return provider


def list_available_providers() -> dict[str, dict]:
    """Return a summary of all available providers and their models.

    Useful for CLI help or API introspection.
    """
    result = {}
    for name, preset in PRESETS.items():
        result[name] = {
            "display_name": preset.display_name,
            "default_model": preset.default_model,
            "models": list(preset.models.keys()),
            "sdk": preset.sdk,
            "note": preset.note,
        }
    return result
