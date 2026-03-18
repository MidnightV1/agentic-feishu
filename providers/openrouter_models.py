# -*- coding: utf-8 -*-
"""OpenRouter model catalog — runtime query with local cache.

OpenRouter's /api/v1/models endpoint is public (no API key needed) and
returns pricing, context_length, supported_parameters for all 300+ models.

This module:
  1. Fetches the full catalog on first use (or cache miss)
  2. Caches to disk with TTL (default 24h)
  3. Provides typed lookups for any model ID

Usage:
    catalog = await OpenRouterCatalog.load()
    info = catalog.get("anthropic/claude-sonnet-4")
    info.context_window     # 200000
    info.input_price_per_m  # 3.0  (USD per 1M tokens)
    info.tool_support       # True
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_TTL = 86400  # 24 hours
DEFAULT_CACHE_PATH = "data/openrouter_models.json"


@dataclass
class OpenRouterModelInfo:
    """Parsed model metadata from OpenRouter API."""
    id: str
    name: str
    context_window: int
    max_completion_tokens: int
    # Pricing: USD per token (float) — multiply by 1_000_000 for per-M rate
    input_price_per_token: float
    output_price_per_token: float
    cache_read_price_per_token: float
    cache_write_price_per_token: float
    # Capabilities
    tool_support: bool
    vision: bool
    reasoning: bool
    modality: str  # "text->text", "text+image->text", etc.

    @property
    def input_price_per_m(self) -> float:
        """USD per 1M input tokens."""
        return self.input_price_per_token * 1_000_000

    @property
    def output_price_per_m(self) -> float:
        """USD per 1M output tokens."""
        return self.output_price_per_token * 1_000_000

    @property
    def cache_read_price_per_m(self) -> float:
        """USD per 1M cached input tokens."""
        return self.cache_read_price_per_token * 1_000_000


class OpenRouterCatalog:
    """Cached model catalog from OpenRouter API."""

    def __init__(self, models: dict[str, OpenRouterModelInfo], fetched_at: float):
        self._models = models
        self._fetched_at = fetched_at

    def get(self, model_id: str) -> OpenRouterModelInfo | None:
        """Look up a model by ID."""
        return self._models.get(model_id)

    def search(self, query: str) -> list[OpenRouterModelInfo]:
        """Search models by name or ID substring."""
        q = query.lower()
        return [m for m in self._models.values() if q in m.id.lower() or q in m.name.lower()]

    def list_by_provider(self, provider: str) -> list[OpenRouterModelInfo]:
        """List all models from a specific provider prefix (e.g. 'anthropic')."""
        prefix = provider.lower().rstrip("/") + "/"
        return [m for m in self._models.values() if m.id.lower().startswith(prefix)]

    @property
    def model_count(self) -> int:
        return len(self._models)

    @property
    def age_hours(self) -> float:
        return (time.time() - self._fetched_at) / 3600

    # ── Loading ──────────────────────────────────────────────

    @classmethod
    async def load(cls, cache_path: str = DEFAULT_CACHE_PATH) -> OpenRouterCatalog:
        """Load catalog from cache, or fetch from API if stale/missing."""
        cache_file = Path(cache_path)

        # Try cache first
        if cache_file.exists():
            try:
                raw = json.loads(cache_file.read_text(encoding="utf-8"))
                fetched_at = raw.get("fetched_at", 0)
                if time.time() - fetched_at < CACHE_TTL:
                    models = {k: _parse_model(v) for k, v in raw.get("models", {}).items()}
                    log.info(
                        "OpenRouter catalog loaded from cache (%d models, %.1fh old)",
                        len(models), (time.time() - fetched_at) / 3600,
                    )
                    return cls(models, fetched_at)
            except Exception:
                log.warning("Cache corrupted, refetching")

        # Fetch from API
        return await cls.fetch(cache_path)

    @classmethod
    async def fetch(cls, cache_path: str = DEFAULT_CACHE_PATH) -> OpenRouterCatalog:
        """Fetch fresh catalog from OpenRouter API."""
        import urllib.request

        log.info("Fetching OpenRouter model catalog...")
        try:
            req = urllib.request.Request(MODELS_URL, headers={"User-Agent": "agentic-feishu/0.1"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            log.exception("Failed to fetch OpenRouter models")
            return cls({}, time.time())

        raw_models = data.get("data", [])
        models: dict[str, OpenRouterModelInfo] = {}
        for raw in raw_models:
            try:
                info = _parse_raw_model(raw)
                models[info.id] = info
            except Exception:
                continue

        fetched_at = time.time()
        log.info("OpenRouter catalog fetched: %d models", len(models))

        # Cache to disk
        cache_file = Path(cache_path)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            cache_data = {
                "fetched_at": fetched_at,
                "models": {k: _serialize_model(v) for k, v in models.items()},
            }
            cache_file.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            log.warning("Failed to write cache")

        return cls(models, fetched_at)

    def format_model_info(self, model_id: str) -> str:
        """Format model info as a human-readable string."""
        info = self.get(model_id)
        if not info:
            return f"Model '{model_id}' not found in OpenRouter catalog"

        lines = [
            f"**{info.name}** (`{info.id}`)",
            f"- Context: {info.context_window:,} tokens",
            f"- Max output: {info.max_completion_tokens:,} tokens",
            f"- Input: ${info.input_price_per_m:.2f}/M tokens",
            f"- Output: ${info.output_price_per_m:.2f}/M tokens",
        ]
        if info.cache_read_price_per_token > 0:
            lines.append(f"- Cache read: ${info.cache_read_price_per_m:.2f}/M tokens")
        caps = []
        if info.tool_support:
            caps.append("tools")
        if info.vision:
            caps.append("vision")
        if info.reasoning:
            caps.append("reasoning")
        if caps:
            lines.append(f"- Capabilities: {', '.join(caps)}")
        return "\n".join(lines)


# ── Parsing helpers ──────────────────────────────────────

def _parse_raw_model(raw: dict[str, Any]) -> OpenRouterModelInfo:
    """Parse a raw model dict from the API response."""
    pricing = raw.get("pricing", {})
    arch = raw.get("architecture", {})
    supported = raw.get("supported_parameters", [])
    top = raw.get("top_provider", {})
    modality = arch.get("modality", "text->text")
    input_modalities = arch.get("input_modalities", [])

    return OpenRouterModelInfo(
        id=raw["id"],
        name=raw.get("name", raw["id"]),
        context_window=raw.get("context_length", 0) or 0,
        max_completion_tokens=top.get("max_completion_tokens", 0) or 0,
        input_price_per_token=float(pricing.get("prompt", 0)),
        output_price_per_token=float(pricing.get("completion", 0)),
        cache_read_price_per_token=float(pricing.get("input_cache_read", 0)),
        cache_write_price_per_token=float(pricing.get("input_cache_write", 0)),
        tool_support="tools" in supported or "tool_choice" in supported,
        vision="image" in input_modalities,
        reasoning="reasoning" in supported or "include_reasoning" in supported,
        modality=modality,
    )


def _parse_model(d: dict) -> OpenRouterModelInfo:
    """Reconstruct from cache dict."""
    return OpenRouterModelInfo(**d)


def _serialize_model(info: OpenRouterModelInfo) -> dict:
    """Serialize for cache."""
    return {
        "id": info.id,
        "name": info.name,
        "context_window": info.context_window,
        "max_completion_tokens": info.max_completion_tokens,
        "input_price_per_token": info.input_price_per_token,
        "output_price_per_token": info.output_price_per_token,
        "cache_read_price_per_token": info.cache_read_price_per_token,
        "cache_write_price_per_token": info.cache_write_price_per_token,
        "tool_support": info.tool_support,
        "vision": info.vision,
        "reasoning": info.reasoning,
        "modality": info.modality,
    }
