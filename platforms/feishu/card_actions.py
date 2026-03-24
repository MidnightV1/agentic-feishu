# -*- coding: utf-8 -*-
"""Card action routing, persistence, and built-in handlers.

Manages the lifecycle of interactive card button clicks:
- SQLite-backed store for action state tracking
- Async router with waiter pattern for blocking workflows
- Built-in handlers for abort, confirm, select, menu, feedback
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Any, Callable

log = logging.getLogger("agentic.feishu.card_actions")


# ---------------------------------------------------------------------------
# CardActionStore -- SQLite persistence
# ---------------------------------------------------------------------------

class CardActionStore:
    """Persistent store for card action states.

    Uses SQLite WAL mode for concurrent read safety. All writes are
    serialized through a single connection (safe for single-process use).
    """

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS actions (
            id            TEXT PRIMARY KEY,
            action_type   TEXT NOT NULL,
            value         TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'pending',
            created_at    REAL NOT NULL,
            resolved_at   REAL,
            resolved_value TEXT
        )
    """

    def __init__(self, db_path: str = "data/card_actions.db"):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle -----------------------------------------------------------

    async def initialize(self) -> None:
        """Create table and configure WAL mode."""
        loop = asyncio.get_running_loop()
        self._conn = await loop.run_in_executor(None, self._open)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(self._CREATE_TABLE)
        conn.commit()
        return conn

    async def close(self) -> None:
        if self._conn:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._conn.close)
            self._conn = None

    # -- CRUD ----------------------------------------------------------------

    async def create(self, action_id: str, action_type: str, value: str = "") -> str:
        """Insert a new pending action. Returns the action_id."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._insert,
            action_id,
            action_type,
            value,
        )
        return action_id

    def _insert(self, action_id: str, action_type: str, value: str) -> None:
        assert self._conn
        self._conn.execute(
            "INSERT OR REPLACE INTO actions (id, action_type, value, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (action_id, action_type, value, time.time()),
        )
        self._conn.commit()

    async def resolve(self, action_id: str, value: str = "") -> bool:
        """Mark an action as resolved. Returns True if it was pending."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._resolve, action_id, value)

    def _resolve(self, action_id: str, value: str) -> bool:
        assert self._conn
        cur = self._conn.execute(
            "UPDATE actions SET status='resolved', resolved_at=?, resolved_value=? "
            "WHERE id=? AND status='pending'",
            (time.time(), value, action_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    async def get_pending(self, action_id: str) -> dict | None:
        """Fetch a pending action by ID, or None."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_pending, action_id)

    def _get_pending(self, action_id: str) -> dict | None:
        assert self._conn
        row = self._conn.execute(
            "SELECT * FROM actions WHERE id=? AND status='pending'",
            (action_id,),
        ).fetchone()
        return dict(row) if row else None

    # -- maintenance ---------------------------------------------------------

    async def expire_stale(self, max_age_hours: float = 1) -> int:
        """Expire pending actions older than *max_age_hours*. Returns count."""
        cutoff = time.time() - max_age_hours * 3600
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._expire, cutoff)

    def _expire(self, cutoff: float) -> int:
        assert self._conn
        cur = self._conn.execute(
            "UPDATE actions SET status='expired', resolved_at=? "
            "WHERE status='pending' AND created_at < ?",
            (time.time(), cutoff),
        )
        self._conn.commit()
        return cur.rowcount

    async def cleanup(self, retention_days: int = 7) -> int:
        """Delete records older than *retention_days*. Returns count."""
        cutoff = time.time() - retention_days * 86400
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._cleanup, cutoff)

    def _cleanup(self, cutoff: float) -> int:
        assert self._conn
        cur = self._conn.execute(
            "DELETE FROM actions WHERE created_at < ?", (cutoff,),
        )
        self._conn.commit()
        return cur.rowcount


# ---------------------------------------------------------------------------
# CardActionRouter -- routing + async waiters
# ---------------------------------------------------------------------------

class CardActionRouter:
    """Routes card button clicks to handlers.

    Usage::

        router = CardActionRouter(store, dispatcher)
        router.register("confirm", my_confirm_handler)

        # In adapter card callback:
        response = await router.handle(action_data)

        # Blocking wait for user input:
        value = await router.create_waiter(action_id, timeout=60)
    """

    def __init__(self, store: CardActionStore, dispatcher: Any):
        self._store = store
        self._dispatcher = dispatcher
        self._handlers: dict[str, Callable] = {}
        self._waiters: dict[str, asyncio.Event] = {}
        self._waiter_values: dict[str, Any] = {}
        # Adapter callbacks -- set via set_adapter() or individually
        self._adapter: Any = None
        self._on_abort: Callable | None = None
        self._on_inject: Callable | None = None
        self._on_feedback: Callable | None = None

    # -- adapter integration -------------------------------------------------

    def set_adapter(self, adapter: Any) -> None:
        """Bind adapter for abort/inject capabilities.

        Expects adapter to have:
        - ``cancel_task(session_key)`` -- abort a running LLM task
        - ``inject_message(chat_id, text, sender_id, sender_name)``
          -- inject a synthetic user message into the conversation
        """
        self._adapter = adapter
        self._on_abort = getattr(adapter, "cancel_task", None)
        self._on_inject = getattr(adapter, "inject_message", None)

    def set_abort_callback(self, cb: Callable) -> None:
        """Register callback for task abort: cb(session_key)."""
        self._on_abort = cb

    def set_inject_callback(self, cb: Callable) -> None:
        """Register callback for message injection: cb(chat_id, text, sender_id, sender_name)."""
        self._on_inject = cb

    def set_feedback_callback(self, cb: Callable) -> None:
        """Register callback for explore feedback: cb(pillar, task_id, feedback)."""
        self._on_feedback = cb

    # -- handler registration ------------------------------------------------

    def register(self, action_type: str, handler: Callable) -> None:
        """Register a handler for *action_type*."""
        self._handlers[action_type] = handler

    def register_builtins(self) -> None:
        """Register all built-in action handlers."""
        self.register("abort_task", self._handle_abort_task)
        self.register("confirm", self._handle_confirm)
        self.register("select", self._handle_select)
        self.register("menu_action", self._handle_menu_action)
        self.register("explore_feedback", self._handle_explore_feedback)

    # -- main dispatch -------------------------------------------------------

    async def handle(self, action_data: dict) -> dict:
        """Process a card action callback from Feishu SDK.

        *action_data* should contain at minimum::

            {
                "action_id": "...",
                "action_type": "confirm",
                "value": {...},        # action-specific payload
                "operator_id": "...",  # who clicked
                "chat_id": "...",      # conversation context
                "message_id": "...",   # card message to update
            }

        Returns a response dict suitable for P2CardActionTriggerResponse
        (usually empty ``{}`` to acknowledge).
        """
        action_id = action_data.get("action_id", "")
        action_type = action_data.get("action_type", "")

        if not action_type:
            log.warning("Card action missing action_type: %s", action_data)
            return {}

        handler = self._handlers.get(action_type)
        if not handler:
            log.warning("No handler for action_type=%s", action_type)
            return {}

        log.info(
            "Card action: type=%s id=%s operator=%s",
            action_type,
            action_id,
            action_data.get("operator_id", "?"),
        )

        try:
            result = await handler(action_data)
        except Exception:
            log.exception("Card action handler error: type=%s", action_type)
            return {}

        # Resolve waiter if someone is waiting on this action_id
        if action_id and action_id in self._waiters:
            resolved_value = action_data.get("value", {})
            self._waiter_values[action_id] = resolved_value
            self._waiters[action_id].set()

        # Resolve in store
        if action_id:
            resolved_str = str(action_data.get("value", ""))
            await self._store.resolve(action_id, resolved_str)

        return result if isinstance(result, dict) else {}

    # -- waiter pattern ------------------------------------------------------

    async def create_waiter(self, action_id: str, timeout: float = 300) -> Any:
        """Wait for a user to click a button. Returns resolved value.

        Raises ``asyncio.TimeoutError`` if no response within *timeout* seconds.
        """
        event = asyncio.Event()
        self._waiters[action_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout)
            return self._waiter_values.pop(action_id, None)
        finally:
            self._waiters.pop(action_id, None)

    # -- built-in handlers ---------------------------------------------------

    async def _handle_abort_task(self, action_data: dict) -> dict:
        """Abort a running LLM task.

        Expected value keys: session_key, chat_id, message_id
        """
        value = action_data.get("value", {})
        session_key = value.get("session_key", "")

        if not session_key:
            log.warning("abort_task: missing session_key")
            return {}

        if self._on_abort:
            try:
                await self._on_abort(session_key)
                log.info("Task aborted: session_key=%s", session_key)
            except Exception:
                log.exception("abort_task callback failed")
                return {}

        # Update card to show aborted status
        message_id = action_data.get("message_id", "")
        if message_id and self._dispatcher:
            await self._dispatcher.update_card_status(
                message_id,
                status_text="\u23f9 \u5df2\u4e2d\u6b62",
                color="grey",
            )

        return {}

    async def _handle_confirm(self, action_data: dict) -> dict:
        """Handle confirm/cancel for dangerous operations.

        Expected value keys: confirmed (bool), chat_id, message_id
        """
        value = action_data.get("value", {})
        confirmed = value.get("confirmed", False)
        message_id = action_data.get("message_id", "")

        # Resolve waiter -- handled in self.handle() above

        # Update card to reflect decision
        if message_id and self._dispatcher:
            if confirmed:
                status = "\u2705 \u5df2\u786e\u8ba4"
                color = "green"
            else:
                status = "\u274c \u5df2\u53d6\u6d88"
                color = "grey"
            await self._dispatcher.update_card_status(
                message_id,
                status_text=status,
                color=color,
            )

        return {}

    async def _handle_select(self, action_data: dict) -> dict:
        """Handle option selection (e.g., analysis mode).

        Expected value keys: selected (str), label (str), message_id
        """
        value = action_data.get("value", {})
        selected = value.get("selected", "")
        label = value.get("label", selected)
        message_id = action_data.get("message_id", "")

        # Resolve waiter with selected value -- handled in self.handle()

        # Update card to show selected option
        if message_id and self._dispatcher:
            await self._dispatcher.update_card_status(
                message_id,
                status_text=f"\u2714 \u5df2\u9009\u62e9: {label}",
                color="blue",
            )

        return {}

    async def _handle_menu_action(self, action_data: dict) -> dict:
        """Quick-menu button -> inject synthetic message into conversation.

        Expected value keys: command (str), chat_id, operator_id, sender_name
        """
        value = action_data.get("value", {})
        command = value.get("command", "")
        chat_id = action_data.get("chat_id", "")
        operator_id = action_data.get("operator_id", "")
        sender_name = value.get("sender_name", "\u7528\u6237")

        if not command or not chat_id:
            log.warning("menu_action: missing command or chat_id")
            return {}

        if self._on_inject:
            try:
                await self._on_inject(chat_id, command, operator_id, sender_name)
                log.info("Menu action injected: command=%s", command)
            except Exception:
                log.exception("menu_action inject failed")
        else:
            log.warning("menu_action: no inject callback configured")

        return {}

    async def _handle_explore_feedback(self, action_data: dict) -> dict:
        """Exploration result feedback (thumbs up/down).

        Expected value keys: feedback ("up"|"down"), pillar (str),
                             task_id (str), message_id
        """
        value = action_data.get("value", {})
        feedback = value.get("feedback", "")
        pillar = value.get("pillar", "")
        task_id = value.get("task_id", "")
        message_id = action_data.get("message_id", "")

        if feedback not in ("up", "down"):
            log.warning("explore_feedback: invalid feedback=%s", feedback)
            return {}

        log.info(
            "Explore feedback: %s pillar=%s task=%s",
            feedback, pillar, task_id,
        )

        # Update card to show feedback received
        if message_id and self._dispatcher:
            emoji = "\U0001f44d" if feedback == "up" else "\U0001f44e"
            await self._dispatcher.update_card_status(
                message_id,
                status_text=f"{emoji} \u53cd\u9988\u5df2\u8bb0\u5f55",
                color="blue",
            )

        # Notify explorer to adjust priority for same-pillar tasks
        if self._on_feedback:
            try:
                await self._on_feedback(pillar, task_id, feedback)
            except Exception:
                log.exception("explore_feedback callback failed")

        return {}
