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
from core.context_manager import ContextComponent, ContextConfig, ContextManager
from core.tool_registry import ToolRegistry
from core.types import RunConfig
from infra.jsonl_store import JSONLStore
from infra.memory import MemoryStore
from infra.session import SessionStore
from infra.user_profile import UserProfileStore
from jobs.explorer import ExploreQueue
from jobs.heartbeat import HeartbeatMonitor
from jobs.scheduler import JobConfig, Scheduler
from platforms.feishu.adapter import FeishuAdapter
from platforms.feishu.api import FeishuAPI
from platforms.feishu.dispatcher import FeishuDispatcher
from platforms.feishu.media import MediaHandler
from platforms.feishu.prompts import FEISHU_SYSTEM_PROMPT
from providers.factory import create_provider
from skills.loader import load_skills
from tools.builtin import general_tools
from tools.builtin import (
    skill_doc, skill_task, skill_cal,
    skill_bitable, skill_sheet, skill_drive, skill_perm,
)

log = logging.getLogger("agentic-feishu")


def _load_template(name: str, subdir: str = "") -> str:
    """Load a markdown template from templates/ directory."""
    base = Path(__file__).parent / "templates"
    if subdir:
        base = base / subdir
    path = base / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    log.warning("Template '%s' not found at %s", name, path)
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

    # ── Feishu API + skill-style tools ────────────────────────────
    feishu_api = FeishuAPI(
        app_id=settings.feishu.app_id,
        app_secret=settings.feishu.app_secret,
    )
    await feishu_api.start()

    _skill_modules = [
        skill_doc, skill_task, skill_cal,
        skill_bitable, skill_sheet, skill_drive, skill_perm,
    ]
    total = 0
    for mod in _skill_modules:
        mod.configure(feishu_api)
        n = registry.discover(mod)
        total += n
    log.info("Registered %d Feishu skill tools", total)

    # ── Custom tools (auto-discover) ─────────────────────────────
    custom_dir = Path(__file__).parent / "tools" / "custom"
    if custom_dir.is_dir():
        n = registry.discover_directory(custom_dir)
        if n:
            log.info("Discovered %d custom tools", n)

    # ── Skills ────────────────────────────────────────────────────
    skills_dir = Path(__file__).parent / "skills"
    skill_registry = load_skills(skills_dir, registry)

    log.info("Tools available: %s", registry.list_tools())

    # ── Context manager ───────────────────────────────────────────
    # If compress_provider is configured, use it as primary compressor
    # with the default provider as fallback (like Hub's Sonnet → Gemini strategy).
    compress_primary = provider
    compress_fallback = None
    if settings.compress_provider:
        compress_primary = create_provider(settings, settings.compress_provider)
        compress_fallback = provider  # default provider as safety net
    context_mgr = ContextManager(
        compress_primary,
        ContextConfig(compress_threshold=settings.context_compress_threshold),
        fallback_provider=compress_fallback,
    )

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

    # ── System prompt (assembled from components) ────────────────
    # Static components (same for all users/sessions)
    soul_text = _load_template("soul.md")
    agent_text = _load_template(f"{settings.persona}.md", subdir="agents")
    if not agent_text:
        # Fallback to old personas/ directory
        agent_text = _load_template(f"{settings.persona}.md", subdir="personas")
    skill_descriptions = skill_registry.build_descriptions()

    # Tool usage guidelines (slimmed — core rules now in tool descriptions)
    guidelines_path = Path(__file__).parent / "templates" / "tool_guidelines.md"
    tool_guidelines = guidelines_path.read_text(encoding="utf-8") if guidelines_path.exists() else ""

    base_components = [
        ContextComponent(type="soul", content=soul_text, priority=100),
        ContextComponent(type="agent", content=agent_text, priority=90),
        ContextComponent(type="platform_rules", content=FEISHU_SYSTEM_PROMPT, priority=85),
        ContextComponent(type="tool_guidelines", content=tool_guidelines, priority=75),
    ]
    if skill_descriptions:
        base_components.append(
            ContextComponent(type="skill_descriptions", content=skill_descriptions, priority=70)
        )

    # Build base system prompt (without per-user components)
    system_prompt = await context_mgr.build_system_prompt(base_components)

    # ── Per-user stores ────────────────────────────────────────────
    memory_store = MemoryStore(os.path.join(settings.data_dir, "memory"))
    user_profile_store = UserProfileStore(os.path.join(settings.data_dir, "users"))

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

    # ── Usage tracker ────────────────────────────────────────────
    from infra.usage import UsageTracker
    usage_tracker = UsageTracker(os.path.join(settings.data_dir, "usage.jsonl"))

    # ── JSONL backup ─────────────────────────────────────────────
    jsonl_store = JSONLStore(os.path.join(settings.data_dir, "sessions"))

    # ── Explore queue ──────────────────────────────────────────────
    explore_queue = ExploreQueue(os.path.join(settings.data_dir, "explore.jsonl"))

    async def _on_explore(hints: str) -> None:
        """Process explore hints from LLM output."""
        count = explore_queue.add_from_hints(hints)
        if count:
            log.info("Added %d explore items from LLM hints", count)

    # ── Media handler ─────────────────────────────────────────────
    media_handler = MediaHandler(api=feishu_api, data_dir=settings.data_dir)

    # ── Provider factory for per-session model switching ──────────
    def _provider_factory(name: str):
        return create_provider(settings, name)

    # ── Feishu adapter ────────────────────────────────────────────
    adapter = FeishuAdapter(
        app_id=settings.feishu.app_id,
        app_secret=settings.feishu.app_secret,
        agent_loop=agent_loop,
        dispatcher=dispatcher,
        session_store=session_store,
        run_config=run_config,
        system_prompt=system_prompt,
        on_explore=_on_explore,
        memory_store=memory_store,
        user_profile_store=user_profile_store,
        context_manager=context_mgr,
        provider_factory=_provider_factory,
        scheduler=scheduler,
    )
    adapter.set_media_handler(media_handler, feishu_api)
    await adapter.start()

    # ── Heartbeat + Scheduler ─────────────────────────────────────
    heartbeat = HeartbeatMonitor(
        usage_tracker=usage_tracker,
        daily_budget_usd=settings.max_budget_usd * 10,  # daily = 10x per-request
    )
    scheduler = Scheduler()
    scheduler.add_job(JobConfig(
        name="heartbeat",
        handler=heartbeat.check,
        interval_seconds=300,  # 5 min
        enabled=True,
    ))
    await scheduler.start()

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
        await scheduler.stop()
        await adapter.stop()
        await dispatcher.stop()
        await feishu_api.stop()
        await session_store.close()
        log.info("agentic-feishu stopped")


if __name__ == "__main__":
    asyncio.run(main())
