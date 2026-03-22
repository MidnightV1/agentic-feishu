# -*- coding: utf-8 -*-
"""Configuration management with YAML file + env var override."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class ProviderConfig(BaseModel):
    api_key: str = ""
    model: str = ""
    base_url: str = ""


class FeishuConfig(BaseModel):
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    notification_bot_app_id: str = ""
    notification_bot_app_secret: str = ""


class BotConfig(BaseModel):
    """Per-bot configuration."""
    name: str = "main"
    app_id: str = ""
    app_secret: str = ""
    provider: str = ""          # override default_provider
    model: str = ""             # override provider default
    default_persona: str = "default"  # template name
    encrypt_key: str = ""


class Settings(BaseModel):
    # Providers — defaults auto-resolved from presets.py at factory level.
    # Users only need to set api_key (and optionally model) in config.yaml.
    # Explicit base_url/model here are overrides; empty = use preset default.
    anthropic: ProviderConfig = ProviderConfig()
    openai: ProviderConfig = ProviderConfig()
    openrouter: ProviderConfig = ProviderConfig()
    deepseek: ProviderConfig = ProviderConfig()
    kimi: ProviderConfig = ProviderConfig()
    qwen: ProviderConfig = ProviderConfig()
    zhipu: ProviderConfig = ProviderConfig()
    minimax: ProviderConfig = ProviderConfig()
    stepfun: ProviderConfig = ProviderConfig()
    baichuan: ProviderConfig = ProviderConfig()
    yi: ProviderConfig = ProviderConfig()
    gemini: ProviderConfig = ProviderConfig()

    # Default provider for main conversation
    default_provider: str = "anthropic"

    # Feishu (legacy single-bot)
    feishu: FeishuConfig = FeishuConfig()

    # Multi-bot (preferred over feishu when present)
    bots: list[BotConfig] = []

    # Agent loop
    max_turns: int = 50
    max_budget_usd: float = 1.0
    temperature: float = 0.7

    # Context
    context_compress_threshold: float = 0.8  # compress at 80% of context window
    compress_provider: str = ""  # empty = use default_provider

    # UI
    show_cost: bool = False
    stream_output: bool = False  # streaming card updates

    # Persona
    persona: str = "default"  # name of persona template

    # Paths
    data_dir: str = "data"
    workspace_dir: str = "workspace"


def _flatten_dict(d: dict, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dict: {'a': {'b': 1}} -> {'a.b': 1}."""
    items: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            items.update(_flatten_dict(v, key))
        else:
            items[key] = v
    return items


def _unflatten_dict(flat: dict[str, Any]) -> dict:
    """Unflatten dotted keys: {'a.b': 1} -> {'a': {'b': 1}}."""
    result: dict = {}
    for key, value in flat.items():
        parts = key.split(".")
        d = result
        for part in parts[:-1]:
            d = d.setdefault(part, {})
        d[parts[-1]] = value
    return result


def _apply_env_overrides(data: dict) -> dict:
    """Override settings from env vars prefixed with AGENTIC_.

    Mapping: AGENTIC_ANTHROPIC_API_KEY -> anthropic.api_key
    """
    prefix = "AGENTIC_"
    flat = _flatten_dict(data)

    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        # AGENTIC_ANTHROPIC_API_KEY -> anthropic.api_key
        setting_key = env_key[len(prefix) :].lower().replace("_", ".")
        # Try exact match first
        if setting_key in flat:
            flat[setting_key] = env_val
            continue
        # Try underscored field names (e.g., AGENTIC_FEISHU_APP_ID -> feishu.app_id)
        # Walk through possible splits: feishu.app.id -> feishu.app_id
        for field_name in _resolve_dotted_key(setting_key, flat):
            flat[field_name] = env_val
            break

    return _unflatten_dict(flat)


def _resolve_dotted_key(dotted: str, known_keys: dict[str, Any]) -> list[str]:
    """Try to match a dotted env key to known settings keys.

    E.g., 'feishu.app.id' should match 'feishu.app_id' if it exists.
    """
    parts = dotted.split(".")
    matches = []
    for known in known_keys:
        known_parts = known.split(".")
        # Check if joining some parts with underscore matches
        if _parts_match(parts, known_parts):
            matches.append(known)
    return matches


def _parts_match(env_parts: list[str], key_parts: list[str]) -> bool:
    """Check if env_parts can be joined to match key_parts.

    E.g., ['feishu', 'app', 'id'] matches ['feishu', 'app_id']
    """
    if not env_parts and not key_parts:
        return True
    if not env_parts or not key_parts:
        return False

    # Try consuming 1, 2, 3... env_parts to match the first key_part
    for n in range(1, len(env_parts) + 1):
        candidate = "_".join(env_parts[:n])
        if candidate == key_parts[0]:
            if _parts_match(env_parts[n:], key_parts[1:]):
                return True
    return False


def load_settings(config_path: str = "config.yaml") -> Settings:
    """Load settings from YAML file, with env var overrides.

    Env vars: AGENTIC_ANTHROPIC_API_KEY, AGENTIC_FEISHU_APP_ID, etc.
    """
    data: dict = {}
    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                data = loaded

    # Build defaults from Settings model to populate known keys for env matching
    defaults = Settings().model_dump()
    merged = _deep_merge(defaults, data)
    merged = _apply_env_overrides(merged)

    return Settings(**merged)


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
