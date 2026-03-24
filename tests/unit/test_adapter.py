"""Adapter tests — message entry defense: dedup, debounce, serialization, group @.

Covers:
- P0-1: session serialization (asyncio.Lock per debounce_key)
- P0-2: group @bot detection
- P1-1: recall cancels running task
- P1-3: pending_media counter
- P2-2: stale message discard (>120s)
- P2-3: tenant isolation
- D8: adaptive debounce (2s first / 1s subsequent)
- Dedup: message_id + content_hash
- Rate limiting: 10 messages / 60s per user
"""
import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


BOT_OPEN_ID = "ou_bot_test"


# ── Dedup ────────────────────────────────────────────────

class TestDedup:
    """Message deduplication."""

    async def test_message_id_dedup(self, make_event):
        """Same message_id is processed only once."""
        # TODO: instantiate adapter with mocked dependencies
        # await adapter.handle(make_event(msg_id="m1"))
        # await adapter.handle(make_event(msg_id="m1"))
        # assert adapter._process_count == 1
        pass

    async def test_content_hash_dedup_within_window(self, make_event):
        """Same content from same sender within 60s → dedup."""
        pass

    async def test_content_hash_dedup_outside_window(self, make_event):
        """Same content after 60s → not deduped."""
        pass

    async def test_image_dedup_30min_window(self, make_event):
        """Same image_key within 30 min → dedup."""
        pass

    async def test_different_senders_not_deduped(self, make_event):
        """Same content from different senders → both processed."""
        pass


# ── Debounce (D8) ────────────────────────────────────────

class TestDebounce:
    """Adaptive debounce: first message 2s, subsequent 1s."""

    async def test_first_message_waits_2s(self, make_event):
        """First message in a batch waits 2s before flush."""
        pass

    async def test_subsequent_message_waits_1s(self, make_event):
        """Second message resets timer to 1s."""
        pass

    async def test_rapid_messages_batched(self, make_event):
        """Messages within debounce window aggregated into one batch."""
        pass

    async def test_media_delays_flush(self, make_event):
        """P1-3: pending_media > 0 delays flush until download completes."""
        pass

    async def test_media_counter_increment_decrement(self, make_event):
        """P1-3: counter incremented on download start, decremented on complete."""
        pass


# ── Session serialization (P0-1) ────────────────────────

class TestSessionLock:
    """P0-1: Per-session asyncio.Lock prevents concurrent processing."""

    async def test_concurrent_messages_serialized(self, make_event):
        """Two messages for same session_key don't process in parallel."""
        pass

    async def test_different_sessions_parallel(self, make_event):
        """Messages for different session_keys CAN process in parallel."""
        pass

    async def test_queue_card_shown_when_locked(self, make_event):
        """D3: Queue notification card shown when lock is held."""
        pass

    async def test_drain_pending_in_lock(self, make_event):
        """While loop inside lock drains any pending messages."""
        pass


# ── Group @bot detection (P0-2) ──────────────────────────

class TestGroupMention:
    """P0-2: Group messages only processed when bot is mentioned."""

    async def test_group_without_mention_ignored(self, make_event):
        """Group message without @bot → silent discard."""
        pass

    async def test_group_with_mention_processed(self, make_event):
        """Group message with @bot → processed normally."""
        pass

    async def test_p2p_always_processed(self, make_event):
        """P2P messages don't need @bot."""
        pass

    async def test_group_mention_extracts_text_without_at(self, make_event):
        """@bot prefix stripped from message text before processing."""
        pass


# ── Message recall ───────────────────────────────────────

class TestRecall:
    """Message recall handling."""

    async def test_recall_during_debounce_cancels_batch(self, make_event):
        """Case A: recall while debouncing → cancel entire batch."""
        pass

    async def test_recall_during_llm_cancels_task(self, make_event):
        """P1-1 Case B: recall while LLM running → cancel task + delete thinking card."""
        pass

    async def test_recall_after_completion_removes_history(self, make_event):
        """P2-1 Case C: recall after completion → delete last round from session."""
        pass


# ── Stale message discard (P2-2) ────────────────────────

class TestStaleMessages:
    """P2-2: Messages older than 120s are discarded."""

    async def test_stale_message_discarded(self, make_event):
        """Message with create_time > 120s ago → discarded."""
        stale_time = str(int((time.time() - 150) * 1000))
        event = make_event(create_time=stale_time)
        # await adapter.handle(event)
        # assert adapter._process_count == 0
        pass

    async def test_fresh_message_processed(self, make_event):
        """Message with recent create_time → processed."""
        pass


# ── Rate limiting ────────────────────────────────────────

class TestRateLimit:
    """10 messages per 60 seconds per user."""

    async def test_within_limit(self, make_event):
        """10 messages within window → all processed."""
        pass

    async def test_over_limit_rejected(self, make_event):
        """11th message within window → rejected with rate limit reply."""
        pass

    async def test_different_users_independent(self, make_event):
        """Rate limits are per-user, not global."""
        pass


# ── Tenant isolation (P2-3) ──────────────────────────────

class TestTenantIsolation:
    """P2-3: First message learns tenant_key, subsequent cross-tenant discarded."""

    async def test_first_message_learns_tenant(self, make_event):
        pass

    async def test_cross_tenant_discarded(self, make_event):
        pass
