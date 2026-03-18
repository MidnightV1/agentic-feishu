# -*- coding: utf-8 -*-
"""agentic-feishu: Native multi-model agent framework for Feishu."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from config.settings import load_settings
from core.agent_loop import AgentLoop
from core.context_manager import ContextConfig, ContextManager
from core.tool_registry import ToolRegistry
from core.types import RunConfig
from infra.session import SessionStore
from platforms.feishu.adapter import FeishuAdapter
from platforms.feishu.api import FeishuAPI
from platforms.feishu.dispatcher import FeishuDispatcher
from providers.factory import create_provider
from tools.builtin import feishu_tools, general_tools

log = logging.getLogger("agentic-feishu")


def _load_persona(name: str) -> str:
    """Load persona template from templates/personas/."""
    persona_dir = Path(__file__).parent / "templates" / "personas"
    path = persona_dir / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    log.warning("Persona '%s' not found at %s, using empty", name, path)
    return ""


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    settings = load_settings()
    log.info(
        "agentic-feishu starting (provider=%s, model=%s)",
        settings.default_provider,
        getattr(getattr(settings, settings.default_provider, None), "model", "?"),
    )

    # ── Provider ──────────────────────────────────────────────────
    provider = create_provider(settings)
    log.info("Provider: %s", provider.name)

    # ── Tool registry ─────────────────────────────────────────────
    registry = ToolRegistry()

    # Auto-discover built-in tools
    n = registry.discover(general_tools)
    log.info("Registered %d general tools", n)

    # ── Feishu API + tools ────────────────────────────────────────
    feishu_api = FeishuAPI(
        app_id=settings.feishu.app_id,
        app_secret=settings.feishu.app_secret,
    )
    await feishu_api.start()
    feishu_tools.configure(feishu_api)
    n = registry.discover(feishu_tools)
    log.info("Registered %d Feishu tools", n)

    log.info("Tools available: %s", registry.list_tools())

    # ── Context manager ───────────────────────────────────────────
    compress_provider = provider
    if settings.compress_provider:
        compress_provider = create_provider(settings, settings.compress_provider)
    context_mgr = ContextManager(compress_provider, ContextConfig(
        compress_threshold=settings.context_compress_threshold,
    ))

    # ── Agent loop ────────────────────────────────────────────────
    agent_loop = AgentLoop(provider, registry, context_mgr)

    # ── Run config ────────────────────────────────────────────────
    provider_cfg = getattr(settings, settings.default_provider, None)
    run_config = RunConfig(
        model=provider_cfg.model if provider_cfg else "",
        provider=settings.default_provider,
        max_turns=settings.max_turns,
        max_budget_usd=settings.max_budget_usd,
        temperature=settings.temperature,
        stream=settings.stream_output,
    )

    # ── System prompt ─────────────────────────────────────────────
    persona_text = _load_persona(settings.persona)
    system_prompt = persona_text

    # ── Session store ─────────────────────────────────────────────
    session_store = SessionStore(
        db_path=os.path.join(settings.data_dir, "sessions.db")
    )
    await session_store.init()

    # ── Dispatcher ────────────────────────────────────────────────
    dispatcher = FeishuDispatcher(
        app_id=settings.feishu.app_id,
        app_secret=settings.feishu.app_secret,
    )
    await dispatcher.start()

    # ── Feishu adapter ────────────────────────────────────────────
    adapter = FeishuAdapter(
        app_id=settings.feishu.app_id,
        app_secret=settings.feishu.app_secret,
        agent_loop=agent_loop,
        dispatcher=dispatcher,
        session_store=session_store,
        run_config=run_config,
        system_prompt=system_prompt,
    )
    await adapter.start()

    log.info("agentic-feishu ready — listening for messages")

    # ── Keep alive ────────────────────────────────────────────────
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        await stop_event.wait()
    finally:
        log.info("Shutting down...")
        await adapter.stop()
        await dispatcher.stop()
        await feishu_api.stop()
        await session_store.close()
        log.info("agentic-feishu stopped")


if __name__ == "__main__":
    asyncio.run(main())
