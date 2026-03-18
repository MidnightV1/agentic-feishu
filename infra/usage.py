# -*- coding: utf-8 -*-
"""Usage tracking — per-session and aggregate token/cost statistics.

Tracks input/output tokens, cost, and model usage. Persists to JSONL for
auditability and provides runtime queries for cost monitoring.

Usage:
    tracker = UsageTracker("data/usage.jsonl")
    tracker.record(session_key, model, usage, cost_usd)

    # Queries
    tracker.daily_total()           # today's spend
    tracker.session_total(key)      # per-session spend
    tracker.check_budget(limit)     # True if over limit
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger("agentic.infra.usage")


@dataclass
class UsageRecord:
    """Single usage event."""
    timestamp: float
    session_key: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# ── Pricing table (per 1M tokens) ───────────────────────────────

_PRICING: dict[str, tuple[float, float]] = {
    # (input_per_M, output_per_M)
    # Anthropic
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    # OpenAI
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o3": (2.0, 8.0),
    "o4-mini": (1.10, 4.40),
    # Gemini
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.15, 0.60),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # Qwen
    "qwen3-max": (1.6, 6.4),
    "qwen3.5-plus": (0.80, 2.0),
    "qwen3.5-flash": (0.20, 0.60),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD based on pricing table."""
    pricing = _PRICING.get(model)
    if not pricing:
        # Try prefix match for OpenRouter format (provider/model)
        short = model.rsplit("/", 1)[-1] if "/" in model else model
        pricing = _PRICING.get(short, (1.0, 3.0))  # conservative default
    inp_rate, out_rate = pricing
    return (input_tokens * inp_rate + output_tokens * out_rate) / 1_000_000


class UsageTracker:
    """Append-only JSONL usage log with in-memory aggregation."""

    def __init__(self, log_path: str | Path = "data/usage.jsonl"):
        self._path = Path(log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[UsageRecord] = []
        self._load()

    def _load(self) -> None:
        """Load existing records from JSONL file."""
        if not self._path.exists():
            return
        try:
            for line in self._path.read_text().splitlines():
                if line.strip():
                    d = json.loads(line)
                    self._records.append(UsageRecord(**d))
        except Exception as e:
            log.warning("Failed to load usage log: %s", e)

    def record(
        self,
        session_key: str,
        model: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        cost_usd: float | None = None,
    ) -> UsageRecord:
        """Record a usage event. Auto-estimates cost if not provided."""
        if cost_usd is None:
            cost_usd = estimate_cost(model, input_tokens, output_tokens)

        rec = UsageRecord(
            timestamp=time.time(),
            session_key=session_key,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cost_usd=cost_usd,
        )
        self._records.append(rec)

        # Append to JSONL
        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(asdict(rec)) + "\n")
        except Exception as e:
            log.error("Failed to write usage record: %s", e)

        log.info(
            "Usage: %s %s in=%d out=%d $%.4f",
            provider, model, input_tokens, output_tokens, cost_usd,
        )
        return rec

    # ── Queries ──────────────────────────────────────────────

    def daily_total(self, day: date | None = None) -> float:
        """Total cost for a given day (default: today)."""
        target = day or date.today()
        return sum(
            r.cost_usd for r in self._records
            if date.fromtimestamp(r.timestamp) == target
        )

    def session_total(self, session_key: str) -> float:
        """Total cost for a session."""
        return sum(r.cost_usd for r in self._records if r.session_key == session_key)

    def daily_summary(self, day: date | None = None) -> dict:
        """Summary stats for a day."""
        target = day or date.today()
        day_recs = [r for r in self._records if date.fromtimestamp(r.timestamp) == target]
        if not day_recs:
            return {"date": str(target), "requests": 0, "cost_usd": 0, "tokens": 0}

        return {
            "date": str(target),
            "requests": len(day_recs),
            "cost_usd": round(sum(r.cost_usd for r in day_recs), 4),
            "input_tokens": sum(r.input_tokens for r in day_recs),
            "output_tokens": sum(r.output_tokens for r in day_recs),
            "models": list({r.model for r in day_recs}),
        }

    def check_budget(self, daily_limit_usd: float) -> bool:
        """Return True if today's spend exceeds the daily limit."""
        return self.daily_total() >= daily_limit_usd
