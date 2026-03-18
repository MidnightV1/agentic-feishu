# -*- coding: utf-8 -*-
"""Provider presets — base_url, models, and defaults for all supported providers.

Users only need to set provider name + API key. Model and endpoint are
auto-resolved from this registry.  Explicit overrides in config.yaml
always take precedence.

Usage:
    from providers.presets import PRESETS, get_preset
    preset = get_preset("deepseek")
    preset.base_url      # "https://api.deepseek.com"
    preset.default_model  # "deepseek-chat"
    preset.models         # {"deepseek-chat": ModelInfo(...), ...}
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelInfo:
    """Metadata for a single model."""
    context_window: int = 128_000
    tool_support: bool = True
    vision: bool = False
    reasoning: bool = False  # thinking/CoT model
    note: str = ""


@dataclass(frozen=True)
class ProviderPreset:
    """Preset configuration for a provider."""
    name: str
    display_name: str
    base_url: str
    default_model: str
    sdk: str = "openai"  # "openai" | "anthropic" | "gemini"
    models: dict[str, ModelInfo] = field(default_factory=dict)
    note: str = ""


# ═══ Provider Registry ═══

PRESETS: dict[str, ProviderPreset] = {

    # ── International ──────────────────────────────────────────

    "openai": ProviderPreset(
        name="openai",
        display_name="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4.1",
        sdk="openai",
        models={
            "gpt-4.1": ModelInfo(context_window=1_000_000),
            "gpt-4.1-mini": ModelInfo(context_window=1_000_000),
            "gpt-4.1-nano": ModelInfo(context_window=1_000_000),
            "o3": ModelInfo(context_window=200_000, reasoning=True),
            "o4-mini": ModelInfo(context_window=200_000, reasoning=True),
        },
    ),

    "anthropic": ProviderPreset(
        name="anthropic",
        display_name="Anthropic",
        base_url="https://api.anthropic.com",
        default_model="claude-sonnet-4-6",
        sdk="anthropic",
        models={
            "claude-opus-4-6": ModelInfo(context_window=200_000),
            "claude-sonnet-4-6": ModelInfo(context_window=200_000),
            "claude-haiku-4-5-20251001": ModelInfo(context_window=200_000),
        },
    ),

    "gemini": ProviderPreset(
        name="gemini",
        display_name="Google Gemini",
        base_url="",  # uses native SDK
        default_model="gemini-2.5-flash",
        sdk="gemini",
        models={
            "gemini-2.5-pro": ModelInfo(context_window=1_000_000),
            "gemini-2.5-flash": ModelInfo(context_window=1_000_000),
        },
    ),

    # ── Aggregator ─────────────────────────────────────────

    "openrouter": ProviderPreset(
        name="openrouter",
        display_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="anthropic/claude-sonnet-4",
        models={
            # Anthropic
            "anthropic/claude-opus-4": ModelInfo(context_window=200_000),
            "anthropic/claude-sonnet-4": ModelInfo(context_window=200_000),
            "anthropic/claude-haiku-4": ModelInfo(context_window=200_000),
            # OpenAI
            "openai/gpt-4.1": ModelInfo(context_window=1_000_000),
            "openai/gpt-4.1-mini": ModelInfo(context_window=1_000_000),
            "openai/o3": ModelInfo(context_window=200_000, reasoning=True),
            "openai/o4-mini": ModelInfo(context_window=200_000, reasoning=True),
            # Google
            "google/gemini-2.5-pro": ModelInfo(context_window=1_000_000),
            "google/gemini-2.5-flash": ModelInfo(context_window=1_000_000),
            # DeepSeek
            "deepseek/deepseek-chat-v3-0324": ModelInfo(context_window=128_000),
            "deepseek/deepseek-r1": ModelInfo(context_window=128_000, reasoning=True),
            # Meta
            "meta-llama/llama-4-maverick": ModelInfo(context_window=1_000_000),
            "meta-llama/llama-4-scout": ModelInfo(context_window=512_000),
            # Qwen
            "qwen/qwen3-235b-a22b": ModelInfo(context_window=128_000),
            "qwen/qwq-32b": ModelInfo(context_window=128_000, reasoning=True),
        },
        note="model IDs use provider/model format; full catalog at openrouter.ai/models",
    ),

    # ── China — Tier 1 (high activity, well-maintained) ──────

    "deepseek": ProviderPreset(
        name="deepseek",
        display_name="DeepSeek 深度求索",
        base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        models={
            "deepseek-chat": ModelInfo(
                context_window=128_000,
                note="DeepSeek-V3 latest, non-thinking mode",
            ),
            "deepseek-reasoner": ModelInfo(
                context_window=128_000,
                reasoning=True,
                note="DeepSeek-V3 thinking/CoT mode",
            ),
        },
    ),

    "kimi": ProviderPreset(
        name="kimi",
        display_name="Kimi (Moonshot)",
        base_url="https://api.moonshot.ai/v1",
        default_model="kimi-k2.5",
        models={
            "kimi-k2.5": ModelInfo(context_window=256_000),
            "kimi-k2": ModelInfo(context_window=128_000),
            "kimi-k2-thinking": ModelInfo(context_window=128_000, reasoning=True),
            "moonshot-v1-128k": ModelInfo(context_window=128_000, note="legacy"),
            "moonshot-v1-32k": ModelInfo(context_window=32_000, note="legacy"),
            "moonshot-v1-8k": ModelInfo(context_window=8_000, note="legacy"),
        },
    ),

    "qwen": ProviderPreset(
        name="qwen",
        display_name="Qwen 通义千问 (DashScope)",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen3-max",
        models={
            "qwen3-max": ModelInfo(context_window=128_000),
            "qwen3.5-plus": ModelInfo(context_window=128_000),
            "qwen3.5-flash": ModelInfo(context_window=128_000),
            "qwen3-coder-plus": ModelInfo(context_window=128_000, note="coding specialist"),
            "qwq-plus": ModelInfo(context_window=128_000, reasoning=True),
            "qwen-max-latest": ModelInfo(context_window=128_000, note="alias"),
            "qwen-plus-latest": ModelInfo(context_window=128_000),
            "qwen-turbo-latest": ModelInfo(context_window=128_000),
        },
    ),

    "zhipu": ProviderPreset(
        name="zhipu",
        display_name="Zhipu AI 智谱",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
        default_model="glm-5",
        models={
            "glm-5": ModelInfo(context_window=202_000, note="flagship, 744B MoE"),
            "glm-4.7": ModelInfo(context_window=128_000),
            "glm-4.6v": ModelInfo(context_window=128_000, vision=True),
            "glm-4.5": ModelInfo(context_window=128_000),
            "glm-4.5-air": ModelInfo(context_window=128_000, note="lightweight"),
            "glm-4.7-flash": ModelInfo(context_window=128_000, note="free tier"),
        },
        note="base_url uses /v4/ (not /v1/) but protocol is OpenAI-compatible",
    ),

    # ── China — Tier 2 ───────────────────────────────────────

    "minimax": ProviderPreset(
        name="minimax",
        display_name="MiniMax",
        base_url="https://api.minimax.io/v1",
        default_model="MiniMax-M2.5",
        models={
            "MiniMax-M2.5": ModelInfo(
                context_window=128_000,
                reasoning=True,
                note="thinking model, ~2.2s extra latency per turn",
            ),
            "MiniMax-M2.5-highspeed": ModelInfo(context_window=128_000, reasoning=True),
            "MiniMax-M2.1": ModelInfo(context_window=128_000),
            "MiniMax-M2.1-highspeed": ModelInfo(context_window=128_000),
        },
        note="thinking model: must include full <think> content in history for coherence",
    ),

    "stepfun": ProviderPreset(
        name="stepfun",
        display_name="Stepfun 阶跃星辰",
        base_url="https://api.stepfun.com/v1",
        default_model="step-3.5-flash",
        models={
            "step-3.5-flash": ModelInfo(
                context_window=256_000,
                note="flagship, 196B MoE, open-source",
            ),
            "step-3": ModelInfo(context_window=128_000, vision=True, note="multimodal"),
            "step-2-16k": ModelInfo(context_window=16_000, note="legacy"),
        },
    ),

    "baichuan": ProviderPreset(
        name="baichuan",
        display_name="Baichuan 百川",
        base_url="https://api.baichuan-ai.com/v1",
        default_model="Baichuan4",
        models={
            "Baichuan4": ModelInfo(context_window=128_000),
            "Baichuan3-Turbo": ModelInfo(context_window=128_000),
            "Baichuan3-Turbo-128k": ModelInfo(context_window=128_000),
        },
        note="model IDs are PascalCase (Baichuan4, not baichuan-4)",
    ),

    "yi": ProviderPreset(
        name="yi",
        display_name="Yi 零一万物 (01.ai)",
        base_url="https://api.lingyiwanwu.com/v1",
        default_model="yi-lightning",
        models={
            "yi-lightning": ModelInfo(context_window=16_000),
            "yi-large": ModelInfo(context_window=32_000),
            "yi-large-turbo": ModelInfo(context_window=16_000),
            "yi-medium": ModelInfo(context_window=16_000),
        },
        note="01.ai stopped pretraining (2025-03); API still runs but no new models expected",
    ),
}


def get_preset(name: str) -> ProviderPreset | None:
    """Look up a provider preset by name."""
    return PRESETS.get(name)


def list_providers() -> list[str]:
    """Return all registered provider names."""
    return list(PRESETS.keys())


def list_models(provider: str) -> list[str]:
    """Return model IDs available for a provider."""
    preset = PRESETS.get(provider)
    if not preset:
        return []
    return list(preset.models.keys())


def get_context_window(provider: str, model: str) -> int:
    """Return context window size for a provider/model combo."""
    preset = PRESETS.get(provider)
    if not preset:
        return 128_000  # safe default
    info = preset.models.get(model)
    return info.context_window if info else 128_000
