# -*- coding: utf-8 -*-
"""Skill loader — discovers skills from directories, configures from global config.

Architecture (CC document-injection mode):
  - Declaration layer: skills/<name>/SKILL.md (frontmatter + body)
  - Config layer: config/skills.yaml (global ops switches)
  - Execution layer: tools/builtin/skill_*.py or skills/<name>/tools.py (CLI scripts)

Loading flow:
  1. Read config/skills.yaml -> global skill config
  2. Scan skills/ subdirectories -> find SKILL.md files
  3. Merge: SKILL.md (name + description) + config (system/enabled/bots/triggers)
  4. Build SkillRegistry with inject_into_context() / get_all_summaries()
  5. LLM invokes skills via bash tool calling CLI scripts - no @tool registration
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("agentic.skills")


@dataclass
class SkillConfig:
    """Parsed skill metadata - supports SKILL.md and config/skills.yaml."""

    name: str
    display_name: str = ""
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    system: bool = False
    enabled: bool = True
    bots: list[str] = field(default_factory=list)
    skill_dir: str = ""
    skill_md_body: str = ""
    tool_name: str = ""
    tools_module: str = ""
    prompts_dir: str = ""
    priority: int = 0

    @property
    def trigger_patterns(self) -> list[re.Pattern]:
        """Compile trigger patterns (cached on first access)."""
        if not hasattr(self, "_compiled"):
            self._compiled = [re.compile(p, re.IGNORECASE) for p in self.triggers]
        return self._compiled

    def is_active(self, bot_id: str | None = None, disabled_skills: set[str] | None = None) -> bool:
        """Return True if this skill should be loaded for the given bot."""
        if self.system:
            pass
        else:
            if not self.enabled:
                return False
            if disabled_skills and self.name in disabled_skills:
                return False
        if self.bots and bot_id and bot_id not in self.bots:
            return False
        return True


class SkillRegistry:
    """Manages skill discovery, registration, and trigger matching.

    CC document-injection API:
      - get_all_summaries() -> skill listing for system prompt
      - inject_into_context(skill_name) -> full SKILL.md body for context injection
      - match_trigger(text) -> trigger-based skill matching
    """

    def __init__(self) -> None:
        self._skills: dict[str, SkillConfig] = {}

    @property
    def skills(self) -> dict[str, SkillConfig]:
        return self._skills

    def register(self, skill: SkillConfig) -> None:
        """Register a skill."""
        self._skills[skill.name] = skill
        log.info(
            "Registered skill: %s (system=%s enabled=%s triggers=%d)",
            skill.name, skill.system, skill.enabled, len(skill.triggers),
        )

    def match_trigger(self, text: str) -> SkillConfig | None:
        """Find the first enabled skill whose trigger matches the text."""
        candidates = sorted(
            [s for s in self._skills.values() if s.enabled],
            key=lambda s: -s.priority,
        )
        for skill in candidates:
            for pattern in skill.trigger_patterns:
                if pattern.search(text):
                    return skill
        return None

    def get_skill(self, name: str) -> SkillConfig | None:
        return self._skills.get(name)

    def list_enabled(self) -> list[SkillConfig]:
        return [s for s in self._skills.values() if s.enabled or s.system]

    # -- CC document-injection API -----------------------------------------

    def inject_into_context(self, skill_name: str) -> str | None:
        """Return the full SKILL.md body for context injection, or None."""
        skill = self._skills.get(skill_name)
        if skill is None:
            normalized = skill_name.replace("-", "_")
            skill = self._skills.get(normalized)
            if skill is None:
                normalized = skill_name.replace("_", "-")
                skill = self._skills.get(normalized)
        if skill is None or not skill.skill_md_body:
            return None
        return skill.skill_md_body

    def get_all_summaries(self) -> str:
        """Generate skill listing for system prompt injection.

        LLM sees this listing and calls skill CLI scripts via bash tool.
        """
        active = [s for s in self._skills.values() if s.enabled or s.system]
        if not active:
            return ""
        lines = ["Available skills (invoke via bash tool with CLI scripts):"]
        for s in sorted(active, key=lambda x: -x.priority):
            label = s.display_name or s.name
            desc = s.description or ""
            lines.append(f"- **{label}**: {desc}")
            if s.triggers:
                triggers = ", ".join(s.triggers[:3])
                lines.append(f"  Triggers: {triggers}")
        return "\n".join(lines)

    def build_descriptions(self) -> str:
        """Legacy alias for get_all_summaries()."""
        return self.get_all_summaries()


# -- SKILL.md parsing -------------------------------------------------------

def _load_skill_md(md_path: Path) -> tuple[dict, str]:
    """Parse a SKILL.md file into (frontmatter_dict, body_str)."""
    text = md_path.read_text(encoding="utf-8")

    if not text.startswith("---"):
        return {}, text.strip()

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text.strip()

    fm_raw = text[3:end].strip()
    body = text[end + 4:].strip()

    try:
        fm = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError:
        log.warning("Failed to parse SKILL.md frontmatter in %s", md_path)
        fm = {}

    return fm, body


# -- Global config loading ---------------------------------------------------

def _load_skills_config(config_path: Path) -> dict[str, dict]:
    """Load config/skills.yaml -> {skill_name: {...}}."""
    if not config_path.exists():
        log.info("No skills config at %s, using defaults", config_path)
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("skills", {})
    except Exception:
        log.exception("Failed to load skills config from %s", config_path)
        return {}


# -- Skill directory builder -------------------------------------------------

def _build_skill_from_dir(entry: Path, global_cfg: dict[str, dict]) -> SkillConfig | None:
    """Build a SkillConfig from a skill directory + global config."""
    md_file = entry / "SKILL.md"
    tools_py = entry / "tools.py"
    prompts = entry / "prompts"
    dir_name = entry.name

    fm: dict[str, Any] = {}
    body: str = ""
    if md_file.exists():
        try:
            fm, body = _load_skill_md(md_file)
        except Exception:
            log.exception("Failed to read SKILL.md in %s", entry)

    name = fm.get("name") or dir_name
    # Normalize name for config lookup: feishu-doc -> feishu_doc
    config_key = name.replace("-", "_")
    cfg = global_cfg.get(name, global_cfg.get(config_key, {}))

    if not md_file.exists() and not cfg:
        return None

    description = fm.get("description", "")
    display_name = fm.get("display_name") or cfg.get("display_name") or name

    system = bool(cfg.get("system", False))
    enabled = bool(cfg.get("enabled", True))
    bots = cfg.get("bots") or []
    triggers = cfg.get("triggers") or []
    priority = int(cfg.get("priority", 0))

    tool_name = cfg.get("tool_name") or name.replace("-", "_")

    return SkillConfig(
        name=name,
        display_name=display_name,
        description=description,
        triggers=triggers,
        system=system,
        enabled=enabled,
        bots=bots,
        skill_dir=str(entry),
        skill_md_body=body,
        tool_name=tool_name,
        tools_module=str(tools_py) if tools_py.exists() else "",
        prompts_dir=str(prompts) if prompts.is_dir() else "",
        priority=priority,
    )


# -- Public API --------------------------------------------------------------

def load_skills(
    skills_dir: str | Path,
    bot_id: str | None = None,
    disabled_skills: list[str] | set[str] | None = None,
    config_path: str | Path | None = None,
) -> SkillRegistry:
    """Discover and load all skills from a directory.

    CC document-injection mode: skills are pure documentation.
    No @tool functions are registered. LLM calls CLI scripts via bash tool.

    Args:
        skills_dir: Root directory to scan for skill subdirectories.
        bot_id: If set, filter by bot_id.
        disabled_skills: Collection of skill names to skip (unless system=True).
        config_path: Path to global skills.yaml.

    Returns:
        SkillRegistry with all discovered (and filtered) skills.
    """
    skill_registry = SkillRegistry()
    skills_path = Path(skills_dir)
    _disabled = set(disabled_skills) if disabled_skills else set()

    if not skills_path.is_dir():
        log.info("Skills directory not found: %s", skills_path)
        return skill_registry

    if config_path is None:
        config_path = skills_path.parent / "config" / "skills.yaml"
    global_cfg = _load_skills_config(Path(config_path))

    for entry in sorted(skills_path.iterdir()):
        if not entry.is_dir():
            continue

        try:
            skill = _build_skill_from_dir(entry, global_cfg)
            if skill is None:
                continue

            if not skill.is_active(bot_id=bot_id, disabled_skills=_disabled):
                log.debug("Skipping skill '%s' (filtered out)", skill.name)
                continue

            skill_registry.register(skill)

        except Exception:
            log.exception("Failed to load skill from %s", entry)

    log.info(
        "Loaded %d skills (%d enabled/system)",
        len(skill_registry.skills),
        len(skill_registry.list_enabled()),
    )
    return skill_registry
