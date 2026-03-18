# -*- coding: utf-8 -*-
"""Feishu platform adapter — WebSocket event handling + agent loop integration.

Connects to Feishu via Lark SDK WebSocket, receives messages,
routes them through the AgentLoop, and sends responses via Dispatcher.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core.agent_loop import AgentLoop
from core.types import Callbacks, Message, RunConfig
from infra.session import SessionStore, SessionRecord
from platforms.feishu.dispatcher import FeishuDispatcher

log = logging.getLogger("agentic.feishu.adapter")

DEBOUNCE_SECONDS = 0.5
DEDUP_TTL = 3600  # 1h


@dataclass
class PendingBatch:
    """Debounce buffer for multi-part messages."""
    parts: list[str] = field(default_factory=list)
    chat_id: str = ""
    chat_type: str = ""
    sender_id: str = ""
    first_message_id: str = ""
    timer: asyncio.Task | None = None


class FeishuAdapter:
    """Bridges Feishu WebSocket events to the AgentLoop.

    Lifecycle:
        adapter = FeishuAdapter(...)
        await adapter.start()  # connects WS
        ...
        await adapter.stop()
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        agent_loop: AgentLoop,
        dispatcher: FeishuDispatcher,
        session_store: SessionStore,
        run_config: RunConfig,
        system_prompt: str = "",
        domain: str = "https://open.feishu.cn",
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.domain = domain
        self._loop = agent_loop
        self._dispatcher = dispatcher
        self._sessions = session_store
        self._run_config = run_config
        self._system_prompt = system_prompt

        self._pending: dict[str, PendingBatch] = {}
        self._seen_ids: dict[str, float] = {}  # message_id → timestamp
        self._ws_client = None
        self._running = False

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Start WebSocket connection to Feishu."""
        import lark_oapi as lark
        from lark_oapi.ws import Client as WsClient

        lark_domain = lark.FEISHU_DOMAIN
        if "larksuite" in self.domain:
            lark_domain = lark.LARK_DOMAIN

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_event)
            .build()
        )

        self._ws_client = (
            WsClient(self.app_id, self.app_secret, event_handler=event_handler, domain=lark_domain)
        )

        self._running = True
        # WsClient.start() is blocking — run in a thread
        asyncio.get_event_loop().run_in_executor(None, self._ws_client.start)
        log.info("Feishu WebSocket adapter started")

    async def stop(self) -> None:
        """Stop the adapter."""
        self._running = False
        # Cancel pending debounce timers
        for batch in self._pending.values():
            if batch.timer:
                batch.timer.cancel()
        self._pending.clear()
        log.info("Feishu adapter stopped")

    # ── Event handling ────────────────────────────────────────────

    def _on_message_event(self, ctx: Any, event: Any) -> None:
        """Lark SDK event callback — dispatched on SDK's thread."""
        try:
            msg = event.event.message
            sender = event.event.sender
            message_id = msg.message_id
            chat_id = msg.chat_id
            chat_type = msg.chat_type
            sender_id = sender.sender_id.open_id if sender.sender_id else ""

            # Dedup
            now = time.time()
            if message_id in self._seen_ids:
                return
            self._seen_ids[message_id] = now
            self._clean_dedup(now)

            # Extract text content
            msg_type = msg.message_type
            if msg_type != "text":
                log.debug("Skipping non-text message type: %s", msg_type)
                return

            content = json.loads(msg.content or "{}")
            text = content.get("text", "").strip()
            if not text:
                return

            # Debounce: buffer messages from same sender in same chat
            key = f"{chat_id}:{sender_id}"
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    self._buffer_message(key, text, chat_id, chat_type, sender_id, message_id)
                )
            )
        except Exception:
            log.exception("Error handling message event")

    async def _buffer_message(
        self,
        key: str,
        text: str,
        chat_id: str,
        chat_type: str,
        sender_id: str,
        message_id: str,
    ) -> None:
        """Add message to debounce buffer, reset timer."""
        batch = self._pending.get(key)
        if batch is None:
            batch = PendingBatch(
                chat_id=chat_id,
                chat_type=chat_type,
                sender_id=sender_id,
                first_message_id=message_id,
            )
            self._pending[key] = batch

        batch.parts.append(text)

        # Reset debounce timer
        if batch.timer:
            batch.timer.cancel()
        batch.timer = asyncio.create_task(self._debounce_flush(key))

    async def _debounce_flush(self, key: str) -> None:
        """Wait for debounce window, then process the batch."""
        await asyncio.sleep(DEBOUNCE_SECONDS)
        batch = self._pending.pop(key, None)
        if not batch:
            return

        combined_text = "\n".join(batch.parts)
        asyncio.create_task(
            self._process_message(
                combined_text, batch.chat_id, batch.sender_id, batch.first_message_id
            )
        )

    # ── Agent loop integration ────────────────────────────────────

    async def _process_message(
        self,
        text: str,
        chat_id: str,
        sender_id: str,
        reply_to: str,
    ) -> None:
        """Run the agent loop and send the response."""
        session_key = f"feishu:{chat_id}:{sender_id}"

        try:
            # Load session history
            session = await self._sessions.get(session_key)
            history_msgs: list[Message] = []

            if session:
                raw = await self._sessions.get_messages(session_key, limit=50)
                for m in raw:
                    history_msgs.append(Message(role=m["role"], content=m["content"]))
            else:
                session = SessionRecord(
                    session_key=session_key,
                    provider=self._run_config.provider,
                    model=self._run_config.model,
                )
                await self._sessions.save(session)

            # Save user message
            await self._sessions.add_message(session_key, "user", text)

            # Streaming text accumulator for card updates
            stream_buf: list[str] = []
            card_id: str | None = None
            last_update = 0.0

            async def on_text(delta: str) -> None:
                nonlocal card_id, last_update
                stream_buf.append(delta)
                now = time.time()
                # Throttle card updates to ~5 QPS
                if now - last_update > 0.2:
                    last_update = now
                    full_text = "".join(stream_buf)
                    if card_id:
                        await self._dispatcher.update_card(card_id, full_text + " ▍")
                    else:
                        card_id = await self._dispatcher.send_card(
                            chat_id, full_text + " ▍", reply_to
                        )

            callbacks = Callbacks(on_text=on_text) if self._run_config.stream else None

            # Run agent loop
            result = await self._loop.run(
                prompt=text,
                config=self._run_config,
                system_prompt=self._system_prompt,
                messages=history_msgs,
                callbacks=callbacks,
            )

            # Send final response
            response_text = result.text or "(no response)"

            if card_id and self._run_config.stream:
                # Update card with final text (remove cursor)
                await self._dispatcher.update_card(card_id, response_text)
            else:
                await self._dispatcher.send_card(chat_id, response_text, reply_to)

            # Save assistant message
            await self._sessions.add_message(session_key, "assistant", response_text)

            # Update session stats
            session.message_count += 2  # user + assistant
            session.total_cost_usd += result.cost_usd
            await self._sessions.save(session)

            log.info(
                "Processed message: turns=%d cost=$%.4f model=%s",
                result.turn_count,
                result.cost_usd,
                self._run_config.model,
            )

        except Exception:
            log.exception("Error processing message from %s in %s", sender_id, chat_id)
            await self._dispatcher.send_card(
                chat_id,
                "{{card:header=处理出错,color=red}}\n抱歉，处理消息时发生错误，请稍后重试。",
                reply_to,
            )

    # ── Housekeeping ──────────────────────────────────────────────

    def _clean_dedup(self, now: float) -> None:
        """Remove expired dedup entries."""
        expired = [k for k, t in self._seen_ids.items() if now - t > DEDUP_TTL]
        for k in expired:
            del self._seen_ids[k]
