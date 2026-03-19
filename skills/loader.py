# -*- coding: utf-8 -*-
"""Skill loader — discovers and registers skills from YAML metadata.

A Skill is a directory under `skills/` containing:
  - skill.yaml  — metadata (name, description, triggers, enabled)
  - prompts/    — optional prompt templates
  - tools.py    — optional tool definitions (@tool decorated)

Skills are discovered at startup, registered into the ToolRegistry,
and their descriptions injected into the system prompt.
"""

from __future__ import annotations

import importlib.util
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.tool_registry import ToolRegistry

log = logging.getLogger("agentic.skills")


@dataclass
class SkillConfig:
    """Parsed skill metadata from skill.yaml."""
    name: str
    display_name: str = ""
    description: str = ""
    triggers: list[str] = field(default_factory=list)  # regex patterns
    enabled: bool = True
    tools_module: str = ""  # path to tools.py if present
    prompts_dir: str = ""   # path to prompts/ if present
    priority: int = 0       # higher = checked first for trigger matching

    @property
    def trigger_patterns(self) -> list[re.Pattern]:
        """Compile trigger patterns (cached on first access)."""
        if not hasattr(self, "_compiled"):
            self._compiled = [re.compile(p, re.IGNORECASE) for p in self.triggers]
        return self._compiled


class SkillRegistry:
    """Manages skill discovery, registration, and trigger matching."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillConfig] = {}

    @property
    def skills(self) -> dict[str, SkillConfig]:
        return self._skills

    def register(self, skill: SkillConfig) -> None:
        """Register a skill."""
        self._skills[skill.name] = skill
        log.info("Registered skill: %s (enabled=%s, triggers=%d)",
                 skill.name, skill.enabled, len(skill.triggers))

    def match_trigger(self, text: str) -> SkillConfig | None:
        """Find the first enabled skill whose trigger matches the text.

        Returns the highest-priority matching skill, or None.
        """
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
        return [s for s in self._skills.values() if s.enabled]

    def build_descriptions(self) -> str:
        """Build skill descriptions for system prompt injection."""
        enabled = self.list_enabled()
        if not enabled:
            return ""
        lines = ["Available skills:"]
        for s in sorted(enabled, key=lambda x: -x.priority):
            triggers = ", ".join(s.triggers[:3])
            lines.append(f"- **{s.display_name or s.name}**: {s.description}")
            if triggers:
                lines.append(f"  Triggers: {triggers}")
        return "\n".join(lines)


def _load_skill_yaml(yaml_path: Path) -> SkillConfig:
    """Parse a skill.yaml file into SkillConfig."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    skill_dir = yaml_path.parent
    tools_py = skill_dir / "tools.py"
    prompts = skill_dir / "prompts"

    return SkillConfig(
        name=data.get("name", skill_dir.name),
        display_name=data.get("display_name", data.get("name", skill_dir.name)),
        description=data.get("description", ""),
        triggers=data.get("triggers", []),
        enabled=data.get("enabled", True),
        tools_module=str(tools_py) if tools_py.exists() else "",
        prompts_dir=str(prompts) if prompts.is_dir() else "",
        priority=data.get("priority", 0),
    )


def _discover_skill_tools(skill: SkillConfig, registry: ToolRegistry) -> int:
    """Import tools.py from a skill directory and register its @tool functions."""
    if not skill.tools_module:
        return 0

    path = Path(skill.tools_module)
    if not path.exists():
        return 0

    try:
        spec = importlib.util.spec_from_file_location(
            f"skill_{skill.name}_tools", str(path)
        )
        if not spec or not spec.loader:
            return 0
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return registry.discover(module)
    except Exception:
        log.exception("Failed to load tools from skill '%s'", skill.name)
        return 0


def load_skills(
    skills_dir: str | Path,
    registry: ToolRegistry | None = None,
) -> SkillRegistry:
    """Discover and load all skills from a directory.

    Each subdirectory with a skill.yaml is treated as a skill.
    If a ToolRegistry is provided, skill tools are auto-registered.

    Returns a SkillRegistry with all discovered skills.
    """
    skill_registry = SkillRegistry()
    skills_path = Path(skills_dir)

    if not skills_path.is_dir():
        log.info("Skills directory not found: %s", skills_path)
        return skill_registry

    for entry in sorted(skills_path.iterdir()):
        if not entry.is_dir():
            continue
        yaml_file = entry / "skill.yaml"
        if not yaml_file.exists():
            continue

        try:
            skill = _load_skill_yaml(yaml_file)
            skill_registry.register(skill)

            if registry and skill.enabled and skill.tools_module:
                n = _discover_skill_tools(skill, registry)
                if n:
                    log.info("Registered %d tools from skill '%s'", n, skill.name)
        except Exception:
            log.exception("Failed to load skill from %s", entry)

    log.info("Loaded %d skills (%d enabled)",
             len(skill_registry.skills),
             len(skill_registry.list_enabled()))
    return skill_registry
