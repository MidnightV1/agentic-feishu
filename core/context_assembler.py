# -*- coding: utf-8 -*-
"""Context assembler — unified prompt assembly from all context layers.

Replaces the hardcoded system prompt construction in main.py.
Pulls from: Platform → Org → Bot → User×Bot → User, in priority order.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.context_manager import ContextComponent, ContextManager, build_recovery_context

if TYPE_CHECKING:
    from infra.bot_context import BotContext
    from infra.org_context import OrgContext
    from infra.session import SessionStore
    from infra.user_bot_relation import UserBotRelation
    from infra.user_profile import UserProfileStore

log = logging.getLogger("agentic.core.context_assembler")


class ContextAssembler:
    """Builds the full system prompt for each conversation turn.

    Assembles context components from all layers:
        [105] Platform rules (Feishu card syntax, XML protocol)
        [100] Org Soul
        [ 98] Org Cognition
        [ 95] Bot Soul
        [ 90] Bot Instructions
        [ 80] User×Bot Persona
        [ 75] Tools + Skills
        [ 70] Bot Shared Knowledge
        [ 68] User×Bot Corrections
        [ 60] User Profile
        [ 50] User×Bot Memory
        [-10] Session Recovery
    """

    def __init__(
        self,
        org: OrgContext,
        bot: BotContext,
        relation: UserBotRelation,
        user_profile: UserProfileStore,
        session_store: SessionStore,
        context_manager: ContextManager,
        platform_prompt: str = "",
        tool_guidelines: str = "",
        skill_descriptions: str = "",
    ):
        self._org = org
        self._bot = bot
        self._relation = relation
        self._user_profile = user_profile
        self._sessions = session_store
        self._context_mgr = context_manager
        self._platform_prompt = platform_prompt
        self._tool_guidelines = tool_guidelines
        self._skill_descriptions = skill_descriptions

    async def build(
        self,
        user_id: str,
        session_key: str,
        is_new_session: bool = False,
    ) -> str:
        """Build the complete system prompt for a conversation turn.

        Args:
            user_id: Feishu open_id of the user.
            session_key: Session key ({bot}:{chat}:{user}).
            is_new_session: If True, include recovery context from prior sessions.

        Returns:
            Assembled system prompt string.
        """
        bot_name = self._bot.name
        components: list[ContextComponent] = []

        # ── Platform (105) ──
        if self._platform_prompt:
            components.append(ContextComponent(
                type="platform", content=self._platform_prompt, priority=105,
            ))

        # ── Org Soul (100) + Cognition (98) ──
        org_soul = self._org.soul
        if org_soul:
            components.append(ContextComponent(
                type="org_soul", content=org_soul, priority=100,
            ))

        org_cog = self._org.cognition
        if org_cog:
            components.append(ContextComponent(
                type="org_cognition", content=org_cog, priority=98,
            ))

        # ── Bot Soul (95) + Instructions (90) ──
        bot_soul = self._bot.soul
        if bot_soul:
            components.append(ContextComponent(
                type="bot_soul", content=bot_soul, priority=95,
            ))

        bot_rules = self._bot.instructions
        if bot_rules:
            components.append(ContextComponent(
                type="bot_rules", content=bot_rules, priority=90,
            ))

        # ── User×Bot Persona (80) ──
        persona = self._relation.get_persona(user_id, bot_name)
        if not persona:
            # First interaction: init from bot default
            default = self._bot.default_persona
            if default:
                persona = self._relation.init_persona(user_id, bot_name, default)
        if persona:
            components.append(ContextComponent(
                type="persona", content=persona, priority=80,
            ))

        # ── Tools + Skills (75) ──
        tool_text = self._tool_guidelines
        if self._skill_descriptions:
            tool_text = f"{tool_text}\n\n{self._skill_descriptions}" if tool_text else self._skill_descriptions
        if tool_text:
            components.append(ContextComponent(
                type="tool_guidelines", content=tool_text, priority=75,
            ))

        # ── Bot Shared Knowledge (70) ──
        shared = self._bot.build_shared_context()
        if shared:
            components.append(ContextComponent(
                type="shared_knowledge", content=shared, priority=70,
            ))

        # ── User×Bot Corrections (68) ──
        corrections = self._relation.build_corrections_context(user_id, bot_name)
        if corrections:
            components.append(ContextComponent(
                type="corrections", content=corrections, priority=68,
            ))

        # ── User Profile (60) ──
        profile = self._user_profile.build_context(user_id)
        if profile:
            components.append(ContextComponent(
                type="user_profile", content=profile, priority=60,
            ))

        # ── User×Bot Memory (50) ──
        memory = self._relation.build_memory_context(user_id, bot_name)
        if memory:
            components.append(ContextComponent(
                type="user_memory", content=memory, priority=50,
            ))

        # ── Session Recovery (-10) ──
        if is_new_session:
            recovery = await build_recovery_context(self._sessions, session_key)
            if recovery:
                components.append(recovery)

        # ── Assemble ──
        return await self._context_mgr.build_system_prompt(components)
