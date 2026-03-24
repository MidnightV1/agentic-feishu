# -*- coding: utf-8 -*-
"""agentic-feishu: Native multi-model agent framework for Feishu."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from config.settings import load_settings, BotConfig
from core.agent_loop import AgentLoop
from core.context_manager import ContextConfig, ContextManager
from core.context_assembler import ContextAssembler
from core.tool_registry import ToolRegistry
from core.types import RunConfig
from infra.jsonl_store import JSONLStore
from infra.bot_context import BotContext
from infra.memory import MemoryStore
from infra.goal_tree import GoalTree
from infra.org_context import OrgContext
from infra.session import SessionStore
from infra.user_bot_relation import UserBotRelation
from infra.user_profile import UserProfileStore
from jobs.explorer import ExploreQueue, ExplorerEngine
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

    # Auto-discover built-in tools (general_tools only — no skill tools)
    n = registry.discover(general_tools)
    log.info("Registered %d general tools", n)

    # ── Feishu API — configure skill modules for in-process use ───
    feishu_api = FeishuAPI(
        app_id=settings.feishu.app_id,
        app_secret=settings.feishu.app_secret,
    )
    await feishu_api.start()

    # Configure API client on skill modules (for in-process calls).
    # No @tool registration — LLM invokes skills via bash tool + CLI scripts.
    _skill_modules = [
        skill_doc, skill_task, skill_cal,
        skill_bitable, skill_sheet, skill_drive, skill_perm,
    ]
    for mod in _skill_modules:
        mod.configure(feishu_api)
    log.info("Configured %d Feishu skill modules (no tool registration)", len(_skill_modules))

    # ── Custom tools (auto-discover) ─────────────────────────────
    custom_dir = Path(__file__).parent / "tools" / "custom"
    if custom_dir.is_dir():
        n = registry.discover_directory(custom_dir)
        if n:
            log.info("Discovered %d custom tools", n)

    # ── Skills (document-injection mode) ──────────────────────────
    skills_dir = Path(__file__).parent / "skills"
    skill_registry = load_skills(skills_dir)

    log.info("Tools available: %s", registry.list_tools())

    # ── Context manager ───────────────────────────────────────────
    compress_primary = provider
    compress_fallback = None
    if settings.compress_provider:
        compress_primary = create_provider(settings, settings.compress_provider)
        compress_fallback = provider
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

    # ── Org context ────────────────────────────────────────────────
    org_context = OrgContext(os.path.join(settings.data_dir, "org"))

    # ── Per-user stores (shared across bots) ──────────────────────
    user_profile_store = UserProfileStore(os.path.join(settings.data_dir, "users"))
    user_bot_relation = UserBotRelation(os.path.join(settings.data_dir, "users"))

    # ── Tool guidelines + skill descriptions ──────────────────────
    guidelines_path = Path(__file__).parent / "templates" / "tool_guidelines.md"
    tool_guidelines = guidelines_path.read_text(encoding="utf-8") if guidelines_path.exists() else ""
    skill_descriptions = skill_registry.build_descriptions()

    # ── Session store (shared, bot_id column for isolation) ──────
    session_store = SessionStore(
        db_path=os.path.join(settings.data_dir, "sessions.db")
    )
    await session_store.init()

    # ── Usage tracker ────────────────────────────────────────────
    from infra.usage import UsageTracker
    usage_tracker = UsageTracker(os.path.join(settings.data_dir, "usage.jsonl"))

    # ── JSONL backup ─────────────────────────────────────────────
    jsonl_store = JSONLStore(os.path.join(settings.data_dir, "sessions"))

    # ── Explore queue ──────────────────────────────────────────────
    explore_queue = ExploreQueue(os.path.join(settings.data_dir, "explore.jsonl"))

    async def _on_explore(hints: str) -> None:
        count = explore_queue.add_from_hints(hints)
        if count:
            log.info("Added %d explore items from LLM hints", count)

    # ── Provider factory ──────────────────────────────────────────
    def _provider_factory(name: str):
        return create_provider(settings, name)

    # ── Explorer engine ──────────────────────────────────────────
    goal_tree = GoalTree(os.path.join(settings.data_dir, "goal_tree.yaml"))
    memory_store = MemoryStore(os.path.join(settings.data_dir, "memory"))

    _explorer_notify_fn = None
    _owner_open_id = os.environ.get("FEISHU_OWNER_OPEN_ID", "")

    explorer_engine = ExplorerEngine(
        queue=explore_queue,
        goal_tree=goal_tree,
        memory_store=memory_store,
        provider_factory=_provider_factory,
        notify_open_id=_owner_open_id,
    )

    # ── Heartbeat + Scheduler ─────────────────────────────────────
    from providers.openai_provider import OpenAIProvider
    _ds_cfg = settings.deepseek
    _heartbeat_provider = None
    if _ds_cfg.api_key:
        _heartbeat_provider = OpenAIProvider(
            api_key=_ds_cfg.api_key,
            model='deepseek-chat',
            base_url=_ds_cfg.base_url or 'https://api.deepseek.com',
            name_override='deepseek',
        )

    heartbeat = HeartbeatMonitor(
        usage_tracker=usage_tracker,
        daily_budget_usd=settings.max_budget_usd * 10,
        triage_provider=_heartbeat_provider,
        action_provider=_heartbeat_provider,
        workspace_dir=str(Path(__file__).parent / settings.workspace_dir),
        explorer=explorer_engine,
    )
    scheduler = Scheduler()
    scheduler.add_job(JobConfig(
        name="heartbeat",
        handler=heartbeat.check,
        interval_seconds=300,
        enabled=True,
    ))
    await scheduler.start()

    # ── Build bot configs (multi-bot or legacy single-bot) ────────
    bot_configs: list[BotConfig] = list(settings.bots)
    if not bot_configs:
        bot_configs = [BotConfig(
            name="main",
            app_id=settings.feishu.app_id,
            app_secret=settings.feishu.app_secret,
        )]

    # ── Start each bot ────────────────────────────────────────────
    adapters: list[FeishuAdapter] = []

    for bot_cfg in bot_configs:
        bot_name = bot_cfg.name
        log.info("Starting bot: %s (app_id=%s...)", bot_name, bot_cfg.app_id[:8] if bot_cfg.app_id else "?")

        bot_context = BotContext(bot_name, os.path.join(settings.data_dir, "bots"))

        bot_provider = bot_cfg.provider or settings.default_provider
        bot_model = bot_cfg.model or getattr(getattr(settings, bot_provider, None), "model", "")
        bot_run_config = RunConfig(
            model=bot_model,
            provider=bot_provider,
            max_turns=settings.max_turns,
            max_budget_usd=settings.max_budget_usd,
            temperature=settings.temperature,
            stream=settings.stream_output,
        )

        bot_provider_instance = create_provider(settings, bot_provider)
        bot_agent_loop = AgentLoop(bot_provider_instance, registry, context_mgr)

        bot_dispatcher = FeishuDispatcher(
            app_id=bot_cfg.app_id,
            app_secret=bot_cfg.app_secret,
        )
        await bot_dispatcher.start()

        bot_feishu_api = FeishuAPI(app_id=bot_cfg.app_id, app_secret=bot_cfg.app_secret)
        await bot_feishu_api.start()
        _pdf_analyzer = None
        if settings.gemini.api_key:
            from google import genai as _genai
            from google.genai import types as _gtypes
            _gclient = _genai.Client(api_key=settings.gemini.api_key)

            async def _analyze_pdf_with_gemini(pdf_bytes: bytes, file_name: str) -> str:
                """Use Gemini Flash to analyze image-based PDF content."""
                resp = await _gclient.aio.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        _gtypes.Content(role="user", parts=[
                            _gtypes.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                            _gtypes.Part.from_text(
                                text=f"请详细提取并总结这份 PDF ({file_name}) 的所有文本内容。"
                                " 如果包含表格，保持表格结构。如果是扫描件，进行 OCR 识别。"
                            ),
                        ]),
                    ],
                    config=_gtypes.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=8192,
                    ),
                )
                if resp.candidates and resp.candidates[0].content.parts:
                    return "\n".join(
                        p.text for p in resp.candidates[0].content.parts if p.text
                    )
                return "[Gemini PDF analysis returned empty]"

            _pdf_analyzer = _analyze_pdf_with_gemini

        bot_media = MediaHandler(
            api=bot_feishu_api,
            data_dir=settings.data_dir,
            pdf_analyzer=_pdf_analyzer,
        )

        assembler = ContextAssembler(
            org=org_context,
            bot=bot_context,
            relation=user_bot_relation,
            user_profile=user_profile_store,
            session_store=session_store,
            context_manager=context_mgr,
            platform_prompt=FEISHU_SYSTEM_PROMPT,
            tool_guidelines=tool_guidelines,
            skill_descriptions=skill_descriptions,
            skill_registry=skill_registry,
        )

        adapter = FeishuAdapter(
            app_id=bot_cfg.app_id,
            app_secret=bot_cfg.app_secret,
            agent_loop=bot_agent_loop,
            dispatcher=bot_dispatcher,
            session_store=session_store,
            run_config=bot_run_config,
            bot_name=bot_name,
            context_assembler=assembler,
            on_explore=_on_explore,
            user_bot_relation=user_bot_relation,
            user_profile_store=user_profile_store,
            context_manager=context_mgr,
            provider_factory=_provider_factory,
            scheduler=scheduler,
        )
        adapter.set_media_handler(bot_media, bot_feishu_api)
        await adapter.start()
        adapters.append(adapter)

        if not explorer_engine._notify and bot_dispatcher:
            explorer_engine._notify = bot_dispatcher.send_to_user
            log.info("Explorer notification wired to bot '%s'", bot_name)

    log.info("All %d bot(s) started", len(adapters))

    log.info("agentic-feishu ready — %d bot(s) listening", len(adapters))

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
        for adapter in adapters:
            await adapter.stop()
        await feishu_api.stop()
        await session_store.close()
        log.info("agentic-feishu stopped")


if __name__ == "__main__":
    asyncio.run(main())
