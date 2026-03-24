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
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from core.agent_loop import AgentLoop
from core.context_manager import ContextManager
from core.types import Callbacks, Message, RunConfig
from infra.session import SessionStore, SessionRecord
from infra.user_bot_relation import UserBotRelation
from infra.user_profile import UserProfileStore
from platforms.feishu.contacts import ContactStore
from platforms.feishu.dispatcher import FeishuDispatcher
from platforms.feishu.media import MediaHandler
from platforms.feishu.tags import wrap_user_input, inject_notifications, parse_output
from tools.builtin._user_context import set_current_user_id

log = logging.getLogger("agentic.feishu.adapter")

DEBOUNCE_FIRST = 2.0   # first message wait (catch doc-share + comment splits)
DEBOUNCE_NEXT = 1.0    # subsequent message wait
DEDUP_TTL = 3600  # 1h
DEDUP_MAX_SIZE = 1000

# Content hash dedup windows (seconds per message type)
_HASH_WINDOWS: dict[str, int] = {
    "text": 60,        # chat messages: 60s
    "image": 1800,     # images: 30min
    "file": 1800,      # files: 30min
    "audio": 1800,     # audio: 30min
}
_HASH_WINDOW_DEFAULT = 60

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

# ── Tool activity labels ─────────────────────────────────────
# Personality-driven progress labels for tool use display.

_TOOL_VERBS: dict[str, list[str]] = {
    "search": ["翻箱倒柜中", "正在搜索", "搜刮中", "大海捞针..."],
    "read": ["翻阅中", "正在读", "啃文档中", "细品中..."],
    "create": ["创作中", "无中生有...", "正在编织", "变出来了..."],
    "update": ["雕花中", "修改中", "外科手术中...", "填坑中..."],
    "delete": ["销毁中", "抹除中...", "正在毁尸灭迹"],
    "list": ["盘点中", "正在清点", "翻抽屉中..."],
    "send": ["投递中", "飞鸽传书...", "正在发射"],
    "bash": ["搞事中...", "正在鞭策主机", "按下了不该按的按钮..."],
    "web": ["网上冲浪中", "正在请教互联网", "百度一下（才怪"],
    "calendar": ["翻日历中", "掐指一算...", "查黄历中..."],
    "task": ["立 flag 中...", "写入小本本...", "先给自己画个饼"],
    "drive": ["翻云盘中", "正在找文件", "整理收纳中..."],
    "bitable": ["查表中", "翻账本...", "数据挖掘中..."],
    "perm": ["发通行证中", "门禁操作...", "授权中..."],
}

_TOOL_ICONS: dict[str, str] = {
    "search": "🔍", "read": "📖", "create": "📝", "update": "✏️",
    "delete": "🗑️", "list": "📂", "send": "📮", "bash": "⚡",
    "web": "🌐", "calendar": "📅", "task": "📋", "drive": "💾",
    "bitable": "📊", "perm": "🔐",
}

_FALLBACK_VERBS = ["搞事情中...", "施法中...", "炼丹中...", "整活中...", "正在变形..."]
_FALLBACK_ICON = "🔧"

# Tool name → category mapping
_TOOL_CATEGORY: dict[str, str] = {
    # Skill-style consolidated tools
    "feishu_doc": "create", "feishu_task": "task", "feishu_cal": "calendar",
    "feishu_bitable": "bitable", "feishu_sheet": "create",
    "feishu_drive": "drive", "feishu_perm": "perm",
    # General tools
    "web_search": "web", "read_file": "read",
    "bash": "bash", "list_directory": "list", "write_file": "create",
}


# Action → category overrides for skill-style tools (action refines the category)
_ACTION_CATEGORY: dict[str, str] = {
    "search": "search", "read": "read", "append": "update",
    "update": "update", "replace_section": "update", "delete": "delete",
    "list": "list", "list_tables": "list", "list_comments": "read",
    "query": "bitable", "add_record": "bitable", "update_record": "bitable",
    "delete_record": "bitable",
    "send_message": "send", "reply_comment": "send",
    "transfer_owner": "perm", "complete": "task",
    "assign": "task", "unassign": "task", "snapshot": "task",
    "freebusy": "calendar", "move": "drive",
    "create_folder": "drive", "info": "read",
    "read_range": "read", "write_range": "update",
    "get_sharing": "perm", "add": "perm", "remove": "perm", "set_sharing": "perm",
}


def _make_tool_label(tool_name: str, arguments: dict | None = None) -> str:
    """Generate a personality-driven progress label for a tool call."""
    cat = _TOOL_CATEGORY.get(tool_name, "")

    # For skill-style tools, refine category by action
    action = (arguments or {}).get("action", "")
    if action and action in _ACTION_CATEGORY:
        cat = _ACTION_CATEGORY[action]

    if not cat:
        for prefix in ("search", "read", "create", "update", "delete", "list", "send"):
            if tool_name.startswith(prefix):
                cat = prefix
                break
    verbs = _TOOL_VERBS.get(cat, _FALLBACK_VERBS)
    icon = _TOOL_ICONS.get(cat, _FALLBACK_ICON)
    verb = random.choice(verbs)

    # Add context from arguments
    detail = ""
    if arguments:
        params = arguments.get("params", arguments)  # skill tools nest in params
        detail = (params.get("query") or params.get("summary")
                  or params.get("title") or params.get("document_id")
                  or params.get("command") or "")
        if isinstance(detail, str) and len(detail) > 25:
            detail = detail[:22] + "…"

    if detail:
        return f"{icon} {verb}「{detail}」"
    return f"{icon} {verb}"


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
        bot_name: str = "main",
        context_assembler: Any = None,  # core.context_assembler.ContextAssembler
        domain: str = "https://open.feishu.cn",
        usage_tracker: Any = None,
        on_explore: Any = None,
        on_task_plan: Any = None,
        user_bot_relation: UserBotRelation | None = None,
        user_profile_store: UserProfileStore | None = None,
        context_manager: ContextManager | None = None,
        provider_factory: Any = None,
        scheduler: Any = None,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.domain = domain
        self._loop = agent_loop
        self._dispatcher = dispatcher
        self._usage = usage_tracker
        self._sessions = session_store
        self._run_config = run_config
        self._bot_name = bot_name
        self._assembler = context_assembler
        self._on_explore = on_explore
        self._on_task_plan = on_task_plan
        self._relation = user_bot_relation
        self._user_profile = user_profile_store
        self._context_mgr = context_manager
        self._media: MediaHandler | None = None
        self._feishu_api: Any = None  # set by set_media_handler
        self._contacts = ContactStore()

        self._pending: dict[str, PendingBatch] = {}
        self._seen_ids: dict[str, float] = {}
        self._content_hashes: dict[str, float] = {}  # message_id → timestamp
        self._ws_client = None
        self._running = False
        self._rate_limits: dict[str, list[float]] = {}  # sender_id → [timestamps]
        self._msg_to_session: dict[str, str] = {}  # message_id → session_key
        self._last_reply: dict[str, str] = {}  # session_key → reply card message_id
        self._session_overrides: dict[str, dict] = {}  # session_key → {model, provider}
        self._provider_factory = provider_factory
        self._scheduler = scheduler
        self._provider_cache: dict[str, Any] = {}  # provider_name → provider instance
        self._bot_open_id: str = ""  # populated at start() via bot.info API
        self._tenant_key: str | None = None  # P2-3: tenant isolation (auto-learned)

        # P0-1: session serialization — one lock per debounce_key
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}  # P1-1 recall cancellation

    def set_media_handler(self, media: MediaHandler, api: Any = None) -> None:
        """Set the media handler for processing images/files/audio."""
        self._media = media
        self._feishu_api = api
        if api:
            self._contacts.set_api(api)
            # Expose ContactStore to skill tools
            from tools.builtin._user_context import set_contacts
            set_contacts(self._contacts)

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
            .register_p2_im_message_recalled_v1(self._on_recall_event)
            .register_p2_card_action_trigger(self._on_card_action_sync)
            .build()
        )

        self._ws_client = (
            WsClient(self.app_id, self.app_secret, event_handler=event_handler, domain=lark_domain)
        )

        self._running = True
        self._loop_ref = asyncio.get_running_loop()

        # Fetch bot open_id for group @mention filtering
        try:
            if self._feishu_api:
                resp = await self._feishu_api.request("GET", "/open-apis/bot/v3/info")
                self._bot_open_id = (resp.get("bot") or {}).get("open_id", "")
                if self._bot_open_id:
                    log.info("Bot open_id: %s", self._bot_open_id)
                else:
                    log.warning("Could not fetch bot open_id — group @mention filter disabled")
        except Exception:
            log.warning("Failed to fetch bot info, group @mention filter disabled", exc_info=True)

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
        """Check WebSocket health with exponential backoff. Exit if unrecoverable.

        Lark SDK bug: disconnection leaves _select() spinning forever
        without reconnecting. Force exit to let process manager restart.
        """
        await asyncio.sleep(30)  # initial grace period
        consecutive_failures = 0
        while self._running:
            check_interval = min(30 * (2 ** consecutive_failures), 120)  # 30s → 60s → 120s cap
            await asyncio.sleep(check_interval if consecutive_failures == 0 else 30)
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

                dead_seconds = sum(30 * (2 ** i) for i in range(consecutive_failures))
                if consecutive_failures >= 3:
                    log.error(
                        "WebSocket dead for ~%ds (%d consecutive failures), "
                        "exiting for process restart",
                        dead_seconds, consecutive_failures,
                    )
                    import sys
                    sys.exit(1)
                else:
                    log.warning(
                        "WebSocket unhealthy (failure %d/3, ~%ds elapsed)",
                        consecutive_failures, dead_seconds,
                    )
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

            # Group messages: only process if bot is @mentioned
            if chat_type == "group" and self._bot_open_id:
                mentions = getattr(msg, "mentions", None) or []
                bot_mentioned = any(
                    getattr(getattr(m, "id", None), "open_id", None) == self._bot_open_id
                    for m in mentions
                )
                if not bot_mentioned:
                    return  # group message without @bot, silently discard

            # Dedup
            now = time.time()
            if message_id in self._seen_ids:
                return
            self._seen_ids[message_id] = now
            self._clean_dedup(now)

            # Tenant isolation (P2-3)
            tenant_key = getattr(data.event, 'tenant_key', None) or ''
            if self._tenant_key is None and tenant_key:
                self._tenant_key = tenant_key
                log.info("Learned tenant_key: %s", tenant_key[:8])
            elif self._tenant_key and tenant_key and tenant_key != self._tenant_key:
                log.debug("Cross-tenant message discarded: %s", msg_id)
                return

            # Auto-learn sender identity (async, fire-and-forget)
            if sender_id:
                try:
                    loop = asyncio.get_event_loop()
                    loop.call_soon_threadsafe(
                        lambda sid=sender_id: asyncio.ensure_future(
                            self._contacts.auto_learn(sid)
                        )
                    )
                except Exception:
                    pass

            # Extract content based on message type
            msg_type = msg.message_type
            content = json.loads(msg.content or "{}")

            # Content hash dedup (Layer 2: same content within time window)
            content_raw = msg.content or "{}"
            c_hash = hashlib.sha256(
                f"{sender_id}:{msg_type}:{content_raw}".encode()
            ).hexdigest()[:16]
            window = _HASH_WINDOWS.get(msg_type, _HASH_WINDOW_DEFAULT)
            prev_ts = self._content_hashes.get(c_hash)
            if prev_ts and (now - prev_ts) < window:
                log.debug("Content hash dedup: %s (within %ds window)", c_hash, window)
                return
            self._content_hashes[c_hash] = now

            # Stale message discard (WebSocket reconnect backlog protection)
            create_time_ms = int(getattr(msg, "create_time", None) or "0")
            if create_time_ms > 0:
                age_seconds = (time.time() * 1000 - create_time_ms) / 1000
                if age_seconds > 120:
                    log.debug("Discarding stale message %s (%.0fs old)", message_id, age_seconds)
                    return

            # Rate limit check (per sender)
            if self._check_rate_limit(sender_id):
                log.info("Rate limited sender %s", sender_id)
                return

            # Track message → session for recall handling
            session_key = f"{self._bot_name}:{chat_id}:{sender_id}"
            self._msg_to_session[message_id] = session_key

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
            elif msg_type == "merge_forward" and self._media:
                key = f"{chat_id}:{sender_id}"
                loop = asyncio.get_event_loop()
                loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(
                        self._handle_merge_forward(
                            content, message_id,
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
            sender_name = self._contacts.resolve_id(sender_id) or ""
            batch = PendingBatch(
                chat_id=chat_id,
                chat_type=chat_type,
                sender_id=sender_id,
                sender_name=sender_name,
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
        batch = self._pending.get(key)
        delay = DEBOUNCE_FIRST if (not batch or len(batch.parts) <= 1) else DEBOUNCE_NEXT
        await asyncio.sleep(delay)
        batch = self._pending.get(key)
        if not batch:
            return

        # Wait for in-flight media downloads before flushing
        if batch.pending_media > 0:
            log.debug("Deferring flush for %s: %d media pending", key, batch.pending_media)
            batch.timer = asyncio.create_task(self._debounce_flush(key))
            return

        self._pending.pop(key, None)
        combined_text = "\n".join(batch.parts)
        asyncio.create_task(
            self._serialized_process(key, combined_text, batch)
        )

    # ── Per-user context ─────────────────────────────────────────

    # ── # Command handling ──────────────────────────────────────

    async def _handle_command(
        self,
        text: str,
        chat_id: str,
        sender_id: str,
        reply_to: str,
        session_key: str,
    ) -> None:
        """Handle # commands without invoking the LLM."""
        cmd = text.split()[0].lower()

        if cmd == "#reset":
            await self._sessions.delete(session_key)
            self._session_overrides.pop(session_key, None)
            self._last_reply.pop(session_key, None)
            await self._dispatcher.send_card(
                chat_id,
                "{{card:header=会话已重置,color=green}}\n下条消息开始全新对话。",
                reply_to,
            )

        elif cmd == "#usage":
            session_cost = self._usage.session_total(session_key)
            daily = self._usage.daily_summary()
            models = ", ".join(daily.get("models", [])) or "—"
            msg = (
                "{{card:header=用量统计,color=blue}}\n"
                f"**本轮对话**: ${session_cost:.4f}\n"
                f"**今日总计**: ${daily['cost_usd']:.4f}（{daily['requests']} 次请求）\n"
                f"**Tokens**: {daily.get('input_tokens', 0):,} input / "
                f"{daily.get('output_tokens', 0):,} output\n"
                f"**模型**: {models}"
            )
            await self._dispatcher.send_card(chat_id, msg, reply_to)

        elif cmd == "#menu":
            await self._send_menu_card(chat_id)

        elif cmd == "#jobs":
            if not self._scheduler:
                await self._dispatcher.send_card(chat_id, "调度器未启用。", reply_to)
                return
            from datetime import datetime as _dt
            jobs = self._scheduler.list_jobs(include_disabled=True)
            if not jobs:
                await self._dispatcher.send_card(chat_id, "当前没有定时任务。", reply_to)
                return
            lines = ["{{card:header=定时任务,color=blue}}"]
            for j in jobs:
                status = "✅" if j.enabled else "⚠️"
                sched = j.cron or j.at_time or f"{j.interval_seconds}s"
                next_run = ""
                if j.next_run_at:
                    next_run = _dt.fromtimestamp(j.next_run_at).strftime("%m-%d %H:%M")
                err = f" ❌×{j.consecutive_errors}" if j.consecutive_errors else ""
                lines.append(f"{status} **{j.name}** `{sched}` → {next_run}{err}")
            await self._dispatcher.send_card(chat_id, "\n".join(lines), reply_to)

        elif cmd == "#help":
            msg = (
                "{{card:header=命令列表,color=blue}}\n"
                "| 命令 | 说明 |\n"
                "|------|------|\n"
                "| `#reset` | 重置当前会话 |\n"
                "| `#usage` | 查看用量统计 |\n"
                "| `#model <name>` | 切换模型（如 deepseek、gpt-4.1） |\n"
                "| `#jobs` | 查看定时任务 |\n"
                "| `#menu` | 快捷操作面板 |\n"
                "| `#help` | 显示本帮助 |"
            )
            await self._dispatcher.send_card(chat_id, msg, reply_to)

        elif cmd == "#model":
            parts = text.split(None, 1)
            if len(parts) < 2:
                # Show current model
                override = self._session_overrides.get(session_key, {})
                current_provider = override.get("provider", self._run_config.provider)
                current_model = override.get("model", self._run_config.model)
                await self._dispatcher.send_card(
                    chat_id,
                    f"当前模型: **{current_provider}/{current_model}**\n"
                    f"用法: `#model <provider>` 或 `#model <provider>/<model>`",
                    reply_to,
                )
                return

            target = parts[1].strip()
            if "/" in target:
                provider_name, model_name = target.split("/", 1)
            else:
                provider_name = target
                model_name = ""

            self._session_overrides[session_key] = {
                "provider": provider_name,
                "model": model_name,
            }
            display = f"{provider_name}/{model_name}" if model_name else provider_name
            await self._dispatcher.send_card(
                chat_id,
                f"{{{{card:header=模型已切换,color=green}}}}\n已切换到 **{display}**（本轮会话生效）",
                reply_to,
            )

        else:
            await self._dispatcher.send_card(
                chat_id,
                f"未知命令 `{cmd}`。输入 `#help` 查看可用命令。",
                reply_to,
            )

    # ── Session serialization (P0-1) ──────────────────────────────

    def _get_session_lock(self, key: str) -> asyncio.Lock:
        if key not in self._session_locks:
            self._session_locks[key] = asyncio.Lock()
        # Prevent memory leak: prune unlocked entries above threshold
        if len(self._session_locks) > 100:
            self._session_locks = {
                k: v for k, v in self._session_locks.items()
                if v.locked() or k == key
            }
        return self._session_locks[key]

    async def _serialized_process(
        self, key: str, text: str, batch: PendingBatch,
    ) -> None:
        lock = self._get_session_lock(key)

        if lock.locked():
            await self._send_queue_card(key, batch)

        async with lock:
            task = asyncio.current_task()
            self._running_tasks[key] = task
            try:
                await self._process_message(
                    text,
                    batch.chat_id,
                    batch.chat_type,
                    batch.sender_id,
                    batch.sender_name,
                    batch.first_message_id,
                )
            finally:
                self._running_tasks.pop(key, None)

            # Drain any messages that arrived while lock was held
            while key in self._pending and self._pending[key].parts:
                new_batch = self._pending.pop(key)
                new_text = "\n".join(new_batch.parts)
                await self._process_message(
                    new_text,
                    new_batch.chat_id,
                    new_batch.chat_type,
                    new_batch.sender_id,
                    new_batch.sender_name,
                    new_batch.first_message_id,
                )

    async def _send_queue_card(self, key: str, batch: PendingBatch) -> None:
        try:
            await self._dispatcher.send_card(
                batch.chat_id,
                "{{card:header=排队中,color=grey}}\n前一条消息正在处理，请稍候...",
                batch.first_message_id,
            )
        except Exception:
            pass  # queue hint failure must not block main flow

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
        session_key = f"{self._bot_name}:{chat_id}:{sender_id}"

        # ── Per-session model override ──
        override = self._session_overrides.get(session_key)
        effective_provider = None  # None = use AgentLoop default
        if override:
            from dataclasses import replace as _replace
            effective_config = _replace(
                self._run_config,
                provider=override.get("provider", self._run_config.provider),
                model=override.get("model", self._run_config.model) or self._run_config.model,
            )
            # Resolve provider instance (cached)
            prov_name = override.get("provider", "")
            if prov_name and prov_name != self._run_config.provider and self._provider_factory:
                effective_provider = await self._get_or_create_provider(prov_name)
        else:
            effective_config = self._run_config

        # ── # Command interception ──
        stripped = text.strip()
        if stripped.startswith("#"):
            await self._handle_command(stripped, chat_id, sender_id, reply_to, session_key)
            return

        thinking_id: str | None = None
        pulse_task: asyncio.Task | None = None

        try:
            # Load session history
            session = await self._sessions.get(session_key)
            history_msgs: list[Message] = []

            is_new_session = session is None

            if session:
                raw = await self._sessions.get_messages(session_key, limit=50)
                for m in raw:
                    history_msgs.append(Message(role=m["role"], content=m["content"]))
            else:
                session = SessionRecord(
                    session_key=session_key,
                    bot_id=self._bot_name,
                    provider=effective_config.provider,
                    model=effective_config.model,
                )
                await self._sessions.save(session)

            # Build system prompt from all context layers via assembler
            if self._assembler:
                effective_system = await self._assembler.build(
                    user_id=sender_id,
                    session_key=session_key,
                    is_new_session=is_new_session,
                )
            else:
                effective_system = ""

            # ── Thinking card: immediate feedback ──
            thinking_id = await self._dispatcher.send_card(
                chat_id, "💭 脑子在转…", reply_to
            )

            # ── Input wrapping: <user-input> tag protocol ──
            wrapped_prompt = wrap_user_input(
                text,
                sender_name=sender_name,
                sender_id=sender_id,
                chat_id=chat_id,
                chat_type=chat_type,
            )

            # Save raw user message (unwrapped — tags are transport-layer, not storage)
            await self._sessions.add_message(session_key, "user", text)

            # ── Streaming + pulse state ──
            stream_buf: list[str] = []
            in_tool_phase = [False]  # True while tools are executing
            last_activity = [time.monotonic()]
            start_time = time.monotonic()

            async def _pulse():
                """Background heartbeat: rotate easter eggs when idle."""
                await asyncio.sleep(8)
                while True:
                    elapsed = time.monotonic() - start_time
                    since_activity = time.monotonic() - last_activity[0]
                    # Show idle label when waiting for LLM (not during tool exec or streaming)
                    if since_activity >= 6 and thinking_id and not in_tool_phase[0]:
                        try:
                            await self._dispatcher.update_card(
                                thinking_id, _idle_label(elapsed)
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(8)

            pulse_task = asyncio.create_task(_pulse())

            async def on_text(delta: str) -> None:
                last_activity[0] = time.monotonic()
                in_tool_phase[0] = False
                stream_buf.append(delta)

            tool_trace: list[str] = []  # current round's tool trace

            async def on_tool_start(name: str, arguments=None) -> None:
                last_activity[0] = time.monotonic()
                in_tool_phase[0] = True
                label = _make_tool_label(name, arguments if isinstance(arguments, dict) else None)
                tool_trace.append(label)
                if thinking_id:
                    await self._dispatcher.update_card(
                        thinking_id, "\n".join(tool_trace)
                    )

            async def on_tool_end(name: str, result=None) -> None:
                last_activity[0] = time.monotonic()

            async def on_turn_start(turn: int = 0) -> None:
                """Reset state at the start of each LLM call."""
                in_tool_phase[0] = False
                tool_trace.clear()
                stream_buf.clear()

            # Always register callbacks (provider may upgrade to streaming internally)
            callbacks = Callbacks(
                on_text=on_text,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
                on_turn_start=on_turn_start,
            )

            # Set user context so skill tools can auto-add collaborator
            _user_ctx_token = set_current_user_id(sender_id)

            # Run agent loop (with wrapped prompt)
            result = await self._loop.run(
                prompt=wrapped_prompt,
                config=effective_config,
                system_prompt=effective_system,
                messages=history_msgs,
                callbacks=callbacks,
                provider=effective_provider,
            )

            # Reset user context
            set_current_user_id("")

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

            # ── Fallback: empty content after tool use → summarize ──
            if not reply_text and result.turn_count > 1:
                reply_text = await self._summarize_tool_work(result)
                if reply_text:
                    log.info("Empty content fallback: generated summary from %d turns", result.turn_count)

            # Long content → auto-convert to Feishu doc
            doc_redirect = await self._maybe_convert_to_doc(reply_text, chat_id)
            if doc_redirect:
                reply_text = doc_redirect

            if reply_text:
                if thinking_id:
                    # Replace thinking card with final reply
                    await self._dispatcher.update_card(thinking_id, reply_text)
                    self._last_reply[session_key] = thinking_id
                else:
                    reply_mid = await self._dispatcher.send_card(chat_id, reply_text, reply_to)
                    if reply_mid:
                        self._last_reply[session_key] = reply_mid
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
                    model=effective_config.model,
                    provider=effective_config.provider,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    cached_tokens=result.usage.cached_tokens,
                    cost_usd=result.cost_usd,
                )

            log.info(
                "Processed message: turns=%d cost=$%.4f model=%s/%s",
                result.turn_count,
                result.cost_usd,
                effective_config.provider,
                effective_config.model,
            )

        except Exception as exc:
            log.exception("Error processing message from %s in %s", sender_id, chat_id)
            # Extract the most informative error line
            err_type = type(exc).__name__
            err_msg = str(exc).split("\n")[0][:200]  # first line, cap length
            error_text = (
                "{{card:header=处理出错,color=red}}\n"
                "抱歉，处理消息时发生错误，请稍后重试。\n"
                f"`{err_type}: {err_msg}`"
            )
            if thinking_id:
                await self._dispatcher.update_card(thinking_id, error_text)
            else:
                await self._dispatcher.send_card(chat_id, error_text, reply_to)
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

        # Track in-flight media download
        batch = self._pending.get(key)
        if batch is not None:
            batch.pending_media += 1

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

        finally:
            # Decrement pending_media counter
            batch = self._pending.get(key)
            if batch is not None:
                batch.pending_media = max(0, batch.pending_media - 1)

        if result:
            await self._buffer_message(key, result, chat_id, chat_type, sender_id, message_id)

    async def _handle_merge_forward(
        self,
        content: dict,
        message_id: str,
        key: str,
        chat_id: str,
        chat_type: str,
        sender_id: str,
    ) -> None:
        """Expand merged-forward messages and buffer the text."""
        try:
            texts = await self._media.expand_merged_forward(message_id)
            # Cap at 50 sub-messages
            if len(texts) > 50:
                texts = texts[:50]
                texts.append(f"... [truncated, {len(texts)} sub-messages total]")
            result = "\n---\n".join(texts) if texts else ""
        except Exception as e:
            log.warning("merge_forward expansion failed: %s", e)
            result = f"[合并转发消息处理失败: {e}]"

        if result:
            await self._buffer_message(key, result, chat_id, chat_type, sender_id, message_id)

    # ── Empty-content fallback: summarize tool work ─────────────────

    async def _summarize_tool_work(self, result) -> str | None:
        """When model returns empty content after tool use, make a
        lightweight call to summarize what was done."""
        from core.types import Message as _Msg

        # Collect tool call/result pairs from messages
        tool_log: list[str] = []
        for msg in result.messages:
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    args_preview = str(tc.arguments)[:100]
                    tool_log.append(f"- called `{tc.name}({args_preview})`")
            elif msg.role == "tool":
                content_preview = (msg.content or "")[:200]
                tool_log.append(f"  → result: {content_preview}")

        if not tool_log:
            return None

        summary_prompt = (
            "你刚刚执行了以下工具调用来完成用户的请求，但最终回复为空。"
            "请根据工具调用记录，用简洁的中文总结你完成了什么工作、结果如何。"
            "直接回复总结内容，不需要标签。\n\n"
            + "\n".join(tool_log[-30:])  # cap to last 30 lines
        )

        try:
            summary_result = await self._loop.run(
                prompt=summary_prompt,
                config=self._run_config,
                system_prompt="你是一个简洁的工作汇报助手。根据工具调用记录总结完成了什么。",
                messages=[],
                callbacks=None,
            )
            return summary_result.text.strip() or None
        except Exception as e:
            log.warning("Fallback summary failed: %s", e)
            return None

    # ── Long content → document ────────────────────────────────────

    async def _maybe_convert_to_doc(self, text: str, chat_id: str) -> str | None:
        """If text exceeds threshold, create a Feishu doc and return link.

        Returns the replacement text (with doc link), or None to keep original.
        """
        if not text or not self._feishu_api or len(text) < LONG_CONTENT_THRESHOLD:
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

    # ── Provider cache ─────────────────────────────────────────────

    async def _get_or_create_provider(self, provider_name: str) -> Any:
        """Get or create a provider instance by name (cached)."""
        if provider_name in self._provider_cache:
            return self._provider_cache[provider_name]
        if not self._provider_factory:
            return None
        try:
            provider = self._provider_factory(provider_name)
            self._provider_cache[provider_name] = provider
            log.info("Created provider instance: %s", provider_name)
            return provider
        except Exception:
            log.exception("Failed to create provider: %s", provider_name)
            return None

    # ── Card action handling ────────────────────────────────────────

    def _on_card_action_sync(self, data: Any) -> dict:
        """Lark SDK card action callback (synchronous, on SDK thread).

        Returns an empty card response (acknowledge). Actual processing
        is dispatched to the asyncio loop.
        """
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._handle_card_action(data))
            )
        except Exception:
            log.exception("Error bridging card action")
        # Return empty response to acknowledge
        return {}

    async def _handle_card_action(self, data: Any) -> None:
        """Process a card action (button click) as a synthetic user message."""
        try:
            action = data.event.action
            value = action.value if hasattr(action, "value") else {}
            operator = data.event.operator
            operator_id = operator.open_id if hasattr(operator, "open_id") else ""

            if not value or not operator_id:
                return

            # Menu action: inject the command as a user message
            command = value.get("command", "")
            if not command:
                return

            # Find the chat context from the action event
            # Card actions include the open_message_id which we can use
            # For simplicity, send to operator's DM or use stored chat context
            chat_id = value.get("chat_id", "")
            if not chat_id:
                log.debug("Card action without chat_id context, skipping")
                return

            sender_name = await self._contacts.get_name(operator_id) or "用户"
            log.info("Card action: operator=%s command=%s", operator_id, command)

            # Route as if user typed the command
            await self._process_message(
                command, chat_id, "p2p", operator_id, sender_name, "",
            )
        except Exception:
            log.exception("Card action handler error")

    # ── Menu card ──────────────────────────────────────────────────

    async def _send_menu_card(self, chat_id: str) -> None:
        """Send a quick-action menu card with interactive buttons."""
        buttons = [
            {"text": "📊 用量统计", "type": "default",
             "value": {"command": "#usage", "chat_id": chat_id}},
            {"text": "📅 今日日程", "type": "default",
             "value": {"command": "今天有什么日程？", "chat_id": chat_id}},
            {"text": "🔄 定时任务", "type": "default",
             "value": {"command": "#jobs", "chat_id": chat_id}},
            {"text": "❓ 帮助", "type": "default",
             "value": {"command": "#help", "chat_id": chat_id}},
        ]
        from platforms.feishu.dispatcher import FeishuDispatcher
        btn_group = FeishuDispatcher.build_button_group(buttons, layout="bisected")
        elements = [{"tag": "markdown", "content": "选择一个快捷操作："}]
        elements.extend(btn_group)
        card_json = FeishuDispatcher.build_interactive_card(
            elements, header="快捷操作面板", color="blue",
        )
        await self._dispatcher.send_card_raw(chat_id, card_json)

    # ── Recall handling ────────────────────────────────────────────

    def _on_recall_event(self, data: Any) -> None:
        """Lark SDK recall callback — dispatched on SDK thread."""
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._handle_recall(data))
            )
        except Exception:
            log.exception("Error bridging recall event")

    async def _handle_recall(self, data: Any) -> None:
        """Handle message recall: remove from history + delete reply card."""
        try:
            event = data.event
            message_id = getattr(event, "message_id", None)
            if not message_id:
                log.debug("Recall event without message_id")
                return

            session_key = self._msg_to_session.pop(message_id, None)
            if not session_key:
                log.debug("Recall for unknown message %s", message_id)
                return

            log.info("Recall: message_id=%s session=%s", message_id, session_key)

            # Cancel pending batch if still in debounce
            # (debounce key = chat_id:sender_id, extract from session_key)
            parts = session_key.split(":")
            if len(parts) >= 3:
                debounce_key = f"{parts[1]}:{parts[2]}"
                batch = self._pending.pop(debounce_key, None)
                if batch:
                    if batch.timer:
                        batch.timer.cancel()
                    log.info("Recall: cancelled debounce batch %s", debounce_key)
                    return

                # P1-1: cancel running LLM task if in progress
                task = self._running_tasks.get(debounce_key)
                if task and not task.done():
                    task.cancel()
                    log.info("Recall: cancelled running task for %s", debounce_key)
                    # Delete thinking card if present
                    reply_mid = self._last_reply.pop(session_key, None)
                    if reply_mid:
                        try:
                            await self._dispatcher.delete_message(reply_mid)
                        except Exception:
                            pass
                    return

            # Remove last round from session history
            msgs = await self._sessions.get_recent_messages(session_key, limit=2)
            if msgs and len(msgs) >= 2:
                # Delete the last 2 messages (user + assistant)
                # by getting all messages and re-saving without the last 2
                all_msgs = await self._sessions.get_messages(session_key)
                if len(all_msgs) >= 2:
                    # Delete last 2 messages (user + assistant round)
                    await self._sessions.db.execute(
                        """
                        DELETE FROM messages WHERE rowid IN (
                            SELECT rowid FROM messages
                            WHERE session_key = ?
                            ORDER BY created_at DESC
                            LIMIT 2
                        )
                        """,
                        (session_key,),
                    )
                    await self._sessions.db.commit()
                    log.info("Recall: removed last round for %s", session_key)

            # Delete reply card from Feishu
            reply_mid = self._last_reply.pop(session_key, None)
            if reply_mid:
                await self._dispatcher.delete_message(reply_mid)
                log.info("Recall: deleted reply card %s", reply_mid)

        except Exception:
            log.exception("Recall handler error")

    # ── Rate limiting ──────────────────────────────────────────────

    def _check_rate_limit(self, sender_id: str, max_per_min: int = 10) -> bool:
        """Check per-sender rate limit. Returns True if limit exceeded."""
        now = time.time()
        window = 60.0
        timestamps = self._rate_limits.get(sender_id, [])
        # Prune old entries
        timestamps = [t for t in timestamps if now - t < window]
        self._rate_limits[sender_id] = timestamps

        if len(timestamps) >= max_per_min:
            return True
        timestamps.append(now)
        return False

    # ── Dedup maintenance ──────────────────────────────────────────

    def _clean_dedup(self, now: float) -> None:
        """Remove expired dedup entries and enforce size cap."""
        # Clean expired message IDs
        expired = [k for k, ts in self._seen_ids.items() if now - ts > DEDUP_TTL]
        for k in expired:
            del self._seen_ids[k]
        # Clean expired content hashes
        max_window = max(_HASH_WINDOWS.values()) if _HASH_WINDOWS else DEDUP_TTL
        expired_h = [k for k, ts in self._content_hashes.items() if now - ts > max_window]
        for k in expired_h:
            del self._content_hashes[k]
        # Enforce size cap on both stores
        for store in (self._seen_ids, self._content_hashes):
            if len(store) > DEDUP_MAX_SIZE:
                sorted_items = sorted(store.items(), key=lambda x: x[1])
                for k, _ in sorted_items[: len(store) - DEDUP_MAX_SIZE]:
                    del store[k]


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
