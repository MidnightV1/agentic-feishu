# -*- coding: utf-8 -*-
"""Feishu platform adapter — WebSocket event handling + agent loop integration.

Connects to Feishu via Lark SDK WebSocket, receives messages,
routes them through the AgentLoop, and sends responses via Dispatcher.

Tag protocol integration:
  Input:  user text → wrap_user_input() → <user-input> tagged prompt
  Output: LLM response → parse_output() → reply / explore / task_plan
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from core.agent_loop import AgentLoop
from core.types import Callbacks, Message, RunConfig
from infra.session import SessionStore, SessionRecord
from platforms.feishu.dispatcher import FeishuDispatcher
from platforms.feishu.media import MediaHandler
from platforms.feishu.tags import wrap_user_input, inject_notifications, parse_output

log = logging.getLogger("agentic.feishu.adapter")

DEBOUNCE_SECONDS = 0.5
DEDUP_TTL = 3600  # 1h

# ── Easter egg message pools ──────────────────────────────────

_THINKING_POOL = [
    "腌制中，让想法入味…", "慢炖，用文火…", "酝酿中…", "思考中…",
    "神游中…", "脑子在转…", "在线发呆（其实在想）…", "CPU 过热中…",
    "正在顿悟…", "酝酿思路…", "沉淀中…", "进入意识流…",
    "脑细胞正在开会…", "正在加载人生经验…", "让我消化一下…",
]

_LONG_THINKING = [
    "慢工出细活，不要慌…", "卧槽，大活儿，还得想想…",
    "差点顿悟了，让我冷静下…", "GPU已经起飞…",
    "喝口水，别急，你也喝点，干杯", "Moss，这道题怎么解呀...",
    "正在求助祖师爷...", "神经网络迸发出了灵感...",
    "正在唤醒专家网络...", "正在烧香...", "正在掐指一算...",
    "嘿嘿嘿...", "什么情况...", "？？？", "hummmm...",
    "哦...", "嗯？", "尝试甩锅给CPU...", "正在蒸馏deepseek...",
    "正在和自己对线...", "Warning: 思路溢出...", "脑子：已读不回",
    "正在请求上级支援...", "快了快了（经典谎言）",
    "等等，好像悟了...又没有", "别催，灵感不接受加班",
    "正在向赛博佛祖祈祷...", "道生一，一生二，二生 bug...",
]


def _idle_label(elapsed: float) -> str:
    """Pick a random easter egg based on elapsed time."""
    pool = _LONG_THINKING if elapsed >= 60 else _THINKING_POOL
    label = random.choice(pool)
    if elapsed >= 30:
        m, s = divmod(int(elapsed), 60)
        ts = f"{m}m{s:02d}s" if m else f"{s}s"
        return f"💭 {label} ({ts})"
    return f"💭 {label}"


LONG_CONTENT_THRESHOLD = 3500  # chars — auto-convert to Feishu doc

# Supported media message types
_MEDIA_TYPES = {"image", "file", "audio"}


@dataclass
class PendingBatch:
    """Debounce buffer for multi-part messages."""
    parts: list[str] = field(default_factory=list)
    chat_id: str = ""
    chat_type: str = ""
    sender_id: str = ""
    sender_name: str = ""
    first_message_id: str = ""
    timer: asyncio.Task | None = None
    pending_media: int = 0  # counter for in-flight media downloads


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
        usage_tracker: Any = None,  # infra.usage.UsageTracker (optional)
        on_explore: Any = None,    # async callback(hints: str) for explore processing
        on_task_plan: Any = None,  # async callback(plan_json: str, chat_id: str) for orchestration
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.domain = domain
        self._loop = agent_loop
        self._dispatcher = dispatcher
        self._usage = usage_tracker
        self._sessions = session_store
        self._run_config = run_config
        self._system_prompt = system_prompt
        self._on_explore = on_explore
        self._on_task_plan = on_task_plan
        self._media: MediaHandler | None = None
        self._feishu_api: Any = None  # set by set_media_handler

        self._pending: dict[str, PendingBatch] = {}
        self._seen_ids: dict[str, float] = {}  # message_id → timestamp
        self._ws_client = None
        self._running = False

    def set_media_handler(self, media: MediaHandler, api: Any = None) -> None:
        """Set the media handler for processing images/files/audio."""
        self._media = media
        self._feishu_api = api

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Start WebSocket connection to Feishu.

        Applies three critical monkey-patches for Lark SDK WebSocket stability:
        1. ping_interval cap — server pushes 120s, we cap to 30s
        2. websockets built-in ping disable — conflicts with SDK ping
        3. event loop isolation — module-level loop variable causes cross-instance issues
        """
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
        self._loop_ref = asyncio.get_running_loop()

        # Patch 1: cap ping_interval (server sends 120s, too long → idle disconnect)
        _client = self._ws_client
        _orig_configure = _client._configure

        def _patched_configure(conf):
            _orig_configure(conf)
            if _client._ping_interval > 30:
                _client._ping_interval = 30

        _client._configure = _patched_configure

        def _start_ws():
            # Patch 3: event loop isolation (SDK module-level loop variable)
            import lark_oapi.ws.client as ws_mod
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ws_mod.loop = loop

            # Patch 2: disable websockets built-in ping (conflicts with SDK ping)
            import websockets
            _orig_connect = websockets.connect

            def _patched_ws_connect(uri, **kwargs):
                kwargs.setdefault("ping_interval", None)
                kwargs.setdefault("ping_timeout", None)
                return _orig_connect(uri, **kwargs)

            websockets.connect = _patched_ws_connect
            _client.start()
            websockets.connect = _orig_connect  # restore

        self._loop_ref.run_in_executor(None, _start_ws)
        log.info("Feishu WebSocket adapter started (patches: ping_cap, ws_ping_off, loop_isolation)")

        # Start health monitor
        self._health_task = asyncio.ensure_future(self._ws_health_monitor())

    async def stop(self) -> None:
        """Stop the adapter."""
        self._running = False
        # Cancel health monitor
        if hasattr(self, "_health_task") and self._health_task:
            self._health_task.cancel()
        # Cancel pending debounce timers
        for batch in self._pending.values():
            if batch.timer:
                batch.timer.cancel()
        self._pending.clear()
        log.info("Feishu adapter stopped")

    # ── WS Health Monitor ─────────────────────────────────────────

    async def _ws_health_monitor(self) -> None:
        """Check WebSocket health every 30s. Exit process if dead for 90s.

        Lark SDK bug: disconnection leaves _select() spinning forever
        without reconnecting. Force exit to let process manager restart.
        """
        await asyncio.sleep(30)  # initial grace period
        consecutive_failures = 0
        while self._running:
            await asyncio.sleep(30)
            try:
                conn = getattr(self._ws_client, "_conn", None)
                if conn is None:
                    consecutive_failures += 1
                elif hasattr(conn, "closed") and conn.closed:
                    consecutive_failures += 1
                elif hasattr(conn, "open") and not conn.open:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0
                    continue

                if consecutive_failures >= 3:
                    log.error(
                        "WebSocket dead for %ds, exiting for process restart",
                        consecutive_failures * 30,
                    )
                    import sys
                    sys.exit(1)
            except Exception:
                pass

    # ── Event handling ────────────────────────────────────────────

    def _on_message_event(self, data: Any) -> None:
        """Lark SDK P2 event callback — dispatched on SDK's thread."""
        try:
            msg = data.event.message
            sender = data.event.sender
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

            # Extract content based on message type
            msg_type = msg.message_type
            content = json.loads(msg.content or "{}")

            if msg_type == "text":
                text = content.get("text", "").strip()
                if not text:
                    return
                key = f"{chat_id}:{sender_id}"
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(
                        self._buffer_message(key, text, chat_id, chat_type, sender_id, message_id)
                    )
                )
            elif msg_type in _MEDIA_TYPES and self._media:
                key = f"{chat_id}:{sender_id}"
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(
                        self._handle_media(
                            msg_type, content, message_id,
                            key, chat_id, chat_type, sender_id,
                        )
                    )
                )
            else:
                log.debug("Skipping unsupported message type: %s", msg_type)
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
                combined_text,
                batch.chat_id,
                batch.chat_type,
                batch.sender_id,
                batch.sender_name,
                batch.first_message_id,
            )
        )

    # ── Agent loop integration ────────────────────────────────────

    async def _process_message(
        self,
        text: str,
        chat_id: str,
        chat_type: str,
        sender_id: str,
        sender_name: str,
        reply_to: str,
    ) -> None:
        """Wrap input → thinking card → run agent loop → parse output → route responses."""
        session_key = f"feishu:{chat_id}:{sender_id}"
        thinking_id: str | None = None
        pulse_task: asyncio.Task | None = None

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

            # ── Thinking card: immediate feedback ──
            thinking_id = await self._dispatcher.send_card(
                chat_id, "💭 脑子在转…", reply_to
            )

            # ── Input wrapping: <user-input> tag protocol ──
            wrapped_prompt = wrap_user_input(
                text,
                sender_name=sender_name,
                chat_id=chat_id,
                chat_type=chat_type,
            )

            # Save raw user message (unwrapped — tags are transport-layer, not storage)
            await self._sessions.add_message(session_key, "user", text)

            # ── Streaming + pulse state ──
            stream_buf: list[str] = []
            streaming_started = False
            last_activity = [time.monotonic()]
            start_time = time.monotonic()

            async def _pulse():
                """Background heartbeat: rotate easter eggs when idle."""
                await asyncio.sleep(8)
                while True:
                    elapsed = time.monotonic() - start_time
                    since_activity = time.monotonic() - last_activity[0]
                    # Only update thinking card if we haven't started streaming yet
                    if since_activity >= 6 and thinking_id and not streaming_started:
                        try:
                            await self._dispatcher.update_card(
                                thinking_id, _idle_label(elapsed)
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(8)

            pulse_task = asyncio.create_task(_pulse())

            async def on_text(delta: str) -> None:
                nonlocal streaming_started
                last_activity[0] = time.monotonic()
                stream_buf.append(delta)
                now = time.time()

                if not streaming_started:
                    streaming_started = True

                # Throttle card updates to ~5 QPS
                full_text = "".join(stream_buf)
                preview = _strip_tags_for_preview(full_text)
                if thinking_id and len(preview) > 5:
                    await self._dispatcher.update_card(thinking_id, preview + " ▍")

            async def on_tool_start(name: str, **_kw) -> None:
                last_activity[0] = time.monotonic()
                if thinking_id and not streaming_started:
                    await self._dispatcher.update_card(
                        thinking_id, f"🔧 {name}…"
                    )

            callbacks = Callbacks(
                on_text=on_text,
                on_tool_start=on_tool_start,
            ) if self._run_config.stream else None

            # Run agent loop (with wrapped prompt)
            result = await self._loop.run(
                prompt=wrapped_prompt,
                config=self._run_config,
                system_prompt=self._system_prompt,
                messages=history_msgs,
                callbacks=callbacks,
            )

            # Stop pulse
            if pulse_task:
                pulse_task.cancel()
                pulse_task = None

            # ── Output parsing: tag protocol ──
            raw_response = result.text or ""
            parsed = parse_output(raw_response)

            if parsed.tags_present:
                log.info("Tags found in response: %s", parsed.tags_present)

            # ── Route: task_plan → orchestrator ──
            if parsed.task_plan_json and self._on_task_plan:
                asyncio.create_task(
                    self._on_task_plan(parsed.task_plan_json, chat_id)
                )

            # ── Route: explore hints → async processing ──
            if parsed.explore_hints and self._on_explore:
                asyncio.create_task(
                    self._on_explore(parsed.explore_hints)
                )

            # ── Route: reply → user ──
            reply_text = parsed.reply_text

            # Long content → auto-convert to Feishu doc
            doc_redirect = await self._maybe_convert_to_doc(reply_text, chat_id)
            if doc_redirect:
                reply_text = doc_redirect

            if reply_text:
                if thinking_id:
                    # Replace thinking card with final reply
                    await self._dispatcher.update_card(thinking_id, reply_text)
                else:
                    await self._dispatcher.send_card(chat_id, reply_text, reply_to)
            elif thinking_id:
                await self._dispatcher.update_card(thinking_id, "(处理完成)")

            # Save assistant message (parsed reply, not raw — matches what user sees)
            if reply_text:
                await self._sessions.add_message(session_key, "assistant", reply_text)

            # Update session stats
            session.message_count += 2  # user + assistant
            session.total_cost_usd += result.cost_usd
            await self._sessions.save(session)

            # Record usage
            if self._usage:
                self._usage.record(
                    session_key=session_key,
                    model=self._run_config.model,
                    provider=self._run_config.provider,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    cached_tokens=result.usage.cached_tokens,
                    cost_usd=result.cost_usd,
                )

            log.info(
                "Processed message: turns=%d cost=$%.4f model=%s",
                result.turn_count,
                result.cost_usd,
                self._run_config.model,
            )

        except Exception:
            log.exception("Error processing message from %s in %s", sender_id, chat_id)
            if thinking_id:
                await self._dispatcher.update_card(
                    thinking_id,
                    "{{card:header=处理出错,color=red}}\n抱歉，处理消息时发生错误，请稍后重试。",
                )
            else:
                await self._dispatcher.send_card(
                    chat_id,
                    "{{card:header=处理出错,color=red}}\n抱歉，处理消息时发生错误，请稍后重试。",
                    reply_to,
                )
        finally:
            if pulse_task and not pulse_task.done():
                pulse_task.cancel()

    # ── Media handling ─────────────────────────────────────────────

    async def _handle_media(
        self,
        msg_type: str,
        content: dict,
        message_id: str,
        key: str,
        chat_id: str,
        chat_type: str,
        sender_id: str,
    ) -> None:
        """Process media messages (image/file/audio) and buffer the result."""
        session_key = f"feishu:{chat_id}:{sender_id}"
        result = ""

        try:
            if msg_type == "image":
                image_key = content.get("image_key", "")
                if image_key:
                    result = await self._media.process_image(message_id, image_key, session_key)
            elif msg_type == "file":
                file_key = content.get("file_key", "")
                file_name = content.get("file_name", "unknown")
                if file_key:
                    result = await self._media.process_file(
                        message_id, file_key, file_name, session_key
                    )
            elif msg_type == "audio":
                file_key = content.get("file_key", "")
                if file_key:
                    result = await self._media.process_audio(
                        message_id, file_key, session_key
                    )
        except Exception as e:
            log.warning("Media processing failed: %s", e)
            result = f"[{msg_type} 处理失败: {e}]"

        if result:
            await self._buffer_message(key, result, chat_id, chat_type, sender_id, message_id)

    # ── Long content → document ────────────────────────────────────

    async def _maybe_convert_to_doc(self, text: str, chat_id: str) -> str | None:
        """If text exceeds threshold, create a Feishu doc and return link.

        Returns the replacement text (with doc link), or None to keep original.
        """
        if not self._feishu_api or len(text) < LONG_CONTENT_THRESHOLD:
            return None

        try:
            # Extract title from first heading or first line
            lines = text.strip().split("\n")
            title = "长内容回复"
            for line in lines:
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    title = stripped[:50]
                    break

            doc = await self._feishu_api.create_document(title)
            if "error" in doc:
                return None

            doc_id = doc["document_id"]
            doc_url = doc.get("url", f"https://feishu.cn/docx/{doc_id}")
            await self._feishu_api.append_document(doc_id, text)

            return (
                f"{{{{card:header={title},color=blue}}}}\n"
                f"内容较长，已自动转为文档：[📄 {title}]({doc_url})"
            )
        except Exception as e:
            log.warning("Long content → doc conversion failed: %s", e)
            return None

    # ── Housekeeping ──────────────────────────────────────────────

    def _clean_dedup(self, now: float) -> None:
        """Remove expired dedup entries."""
        expired = [k for k, t in self._seen_ids.items() if now - t > DEDUP_TTL]
        for k in expired:
            del self._seen_ids[k]


# ── Helpers ────────────────────────────────────────────────────

def _strip_tags_for_preview(text: str) -> str:
    """Remove XML tags from streaming preview to avoid showing raw protocol to user.

    During streaming we show a rough preview; final parsing happens after completion.
    """
    import re
    # Remove <next-explore>...</next-explore> blocks
    text = re.sub(r"<next-explore>.*?</next-explore>", "", text, flags=re.DOTALL)
    # Remove <task_plan>...</task_plan> blocks
    text = re.sub(r"<task_plan>.*?</task_plan>", "", text, flags=re.DOTALL)
    # Remove the wrapper tags but keep content
    text = re.sub(r"</?reply-to-user>", "", text)
    return text.strip()
