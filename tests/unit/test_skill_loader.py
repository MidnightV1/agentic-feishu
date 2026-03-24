# -*- coding: utf-8 -*-
"""Unit tests for skill loader: SKILL.md parsing, config merging, trigger matching, filtering."""
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from skills.loader import (
    _load_skill_md,
    _build_skill_from_dir,
    SkillConfig,
    SkillRegistry,
    load_skills,
)


@pytest.fixture
def tmp_skills(tmp_path):
    """Create a temporary skills directory with test skills."""
    return tmp_path / "skills"


def _write_skill(base: Path, name: str, skill_md: str, tools_py: str = ""):
    """Helper to create a skill directory."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(skill_md, encoding="utf-8")
    if tools_py:
        (d / "tools.py").write_text(tools_py, encoding="utf-8")
    return d


# ── _load_skill_md ──


class TestLoadSkillMd:
    def test_valid_frontmatter(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text(textwrap.dedent("""\
            ---
            name: test-skill
            description: A test skill
            ---

            # Body content
            Details here.
        """))
        fm, body = _load_skill_md(md)
        assert fm["name"] == "test-skill"
        assert fm["description"] == "A test skill"
        assert "Body content" in body

    def test_no_frontmatter(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text("Just body content, no frontmatter.")
        fm, body = _load_skill_md(md)
        assert fm == {}
        assert body == "Just body content, no frontmatter."

    def test_malformed_frontmatter(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text("---\n: bad: yaml: {[\n---\nbody")
        fm, body = _load_skill_md(md)
        assert fm == {}
        assert body == "body"

    def test_unclosed_frontmatter(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text("---\nname: test\nno closing delimiter")
        fm, body = _load_skill_md(md)
        assert fm == {}

    def test_empty_frontmatter(self, tmp_path):
        md = tmp_path / "SKILL.md"
        md.write_text("---\n---\nbody only")
        fm, body = _load_skill_md(md)
        assert fm == {}
        assert body == "body only"


# ── SkillConfig ──


class TestSkillConfig:
    def test_is_active_default(self):
        s = SkillConfig(name="test")
        assert s.is_active() is True

    def test_is_active_disabled(self):
        s = SkillConfig(name="test", enabled=False)
        assert s.is_active() is False

    def test_is_active_in_disabled_list(self):
        s = SkillConfig(name="test")
        assert s.is_active(disabled_skills={"test"}) is False

    def test_system_ignores_disabled(self):
        s = SkillConfig(name="test", system=True, enabled=False)
        assert s.is_active() is True

    def test_system_ignores_disabled_list(self):
        s = SkillConfig(name="test", system=True)
        assert s.is_active(disabled_skills={"test"}) is True

    def test_bot_filter(self):
        s = SkillConfig(name="test", bots=["bot_a"])
        assert s.is_active(bot_id="bot_a") is True
        assert s.is_active(bot_id="bot_b") is False

    def test_empty_bots_allows_all(self):
        s = SkillConfig(name="test", bots=[])
        assert s.is_active(bot_id="any") is True

    def test_trigger_patterns(self):
        s = SkillConfig(name="test", triggers=[r"hello", r"\bfoo\b"])
        patterns = s.trigger_patterns
        assert len(patterns) == 2
        assert patterns[0].search("hello world")
        assert patterns[1].search("say foo please")
        assert not patterns[1].search("foobar")


# ── SkillRegistry ──


class TestSkillRegistry:
    def test_register_and_get(self):
        reg = SkillRegistry()
        s = SkillConfig(name="test-skill")
        reg.register(s)
        assert reg.get_skill("test-skill") is s
        assert reg.get_skill("nonexistent") is None

    def test_match_trigger_priority(self):
        reg = SkillRegistry()
        low = SkillConfig(name="low", priority=1, triggers=["hello"])
        high = SkillConfig(name="high", priority=10, triggers=["hello"])
        reg.register(low)
        reg.register(high)
        match = reg.match_trigger("hello world")
        assert match.name == "high"

    def test_match_trigger_no_match(self):
        reg = SkillRegistry()
        s = SkillConfig(name="test", triggers=["specific_word"])
        reg.register(s)
        assert reg.match_trigger("no match here") is None

    def test_match_trigger_skips_disabled(self):
        reg = SkillRegistry()
        s = SkillConfig(name="test", enabled=False, triggers=["hello"])
        reg.register(s)
        assert reg.match_trigger("hello") is None

    def test_list_enabled(self):
        reg = SkillRegistry()
        reg.register(SkillConfig(name="a", enabled=True))
        reg.register(SkillConfig(name="b", enabled=False))
        reg.register(SkillConfig(name="c", system=True, enabled=False))
        enabled = reg.list_enabled()
        names = {s.name for s in enabled}
        assert names == {"a", "c"}

    def test_build_skill_listing(self):
        reg = SkillRegistry()
        reg.register(SkillConfig(name="doc", display_name="feishu-doc",
                                 description="文档操作"))
        listing = reg.build_skill_listing()
        assert "feishu-doc" in listing
        assert "文档操作" in listing

    def test_build_listing_empty(self):
        reg = SkillRegistry()
        assert reg.build_skill_listing() == ""


# ── _build_skill_from_dir ──


class TestBuildSkillFromDir:
    def test_basic_skill(self, tmp_skills):
        d = _write_skill(tmp_skills, "test-skill", textwrap.dedent("""\
            ---
            name: test-skill
            description: Test
            ---
            Body content
        """))
        skill = _build_skill_from_dir(d, {})
        assert skill.name == "test-skill"
        assert skill.description == "Test"
        assert "Body content" in skill.skill_md_body

    def test_config_merge(self, tmp_skills):
        d = _write_skill(tmp_skills, "my-skill", "---\nname: my-skill\n---\nbody")
        cfg = {"my-skill": {"system": True, "priority": 5, "triggers": ["hi"]}}
        skill = _build_skill_from_dir(d, cfg)
        assert skill.system is True
        assert skill.priority == 5
        assert skill.triggers == ["hi"]

    def test_dir_name_fallback(self, tmp_skills):
        d = _write_skill(tmp_skills, "dir-name", "---\n---\nbody")
        skill = _build_skill_from_dir(d, {})
        assert skill.name == "dir-name"

    def test_no_skill_md_no_config_returns_none(self, tmp_skills):
        d = tmp_skills / "empty"
        d.mkdir(parents=True)
        assert _build_skill_from_dir(d, {}) is None

    def test_tool_name_mapping(self, tmp_skills):
        d = _write_skill(tmp_skills, "feishu-doc", "---\nname: feishu-doc\n---\nbody")
        skill = _build_skill_from_dir(d, {})
        assert skill.tool_name == "feishu_doc"

    def test_tools_module_detected(self, tmp_skills):
        d = _write_skill(tmp_skills, "my-skill", "---\nname: my-skill\n---\nbody",
                         tools_py="# empty tools")
        skill = _build_skill_from_dir(d, {})
        assert skill.tools_module.endswith("tools.py")


# ── load_skills (integration) ──


class TestLoadSkills:
    def test_discovers_skills(self, tmp_skills):
        _write_skill(tmp_skills, "skill-a", "---\nname: skill-a\ndescription: A\n---\nbody a")
        _write_skill(tmp_skills, "skill-b", "---\nname: skill-b\ndescription: B\n---\nbody b")
        reg = load_skills(tmp_skills)
        assert len(reg.skills) == 2
        assert reg.get_skill("skill-a") is not None
        assert reg.get_skill("skill-b") is not None

    def test_disabled_skills_filtered(self, tmp_skills):
        _write_skill(tmp_skills, "keep", "---\nname: keep\n---\nbody")
        _write_skill(tmp_skills, "skip", "---\nname: skip\n---\nbody")
        reg = load_skills(tmp_skills, disabled_skills=["skip"])
        assert reg.get_skill("keep") is not None
        assert reg.get_skill("skip") is None

    def test_nonexistent_dir(self, tmp_path):
        reg = load_skills(tmp_path / "nope")
        assert len(reg.skills) == 0

    def test_empty_dir(self, tmp_skills):
        tmp_skills.mkdir(parents=True)
        reg = load_skills(tmp_skills)
        assert len(reg.skills) == 0

    def test_bot_filter(self, tmp_skills):
        _write_skill(tmp_skills, "restricted", "---\nname: restricted\n---\nbody")
        cfg_path = tmp_skills.parent / "config" / "skills.yaml"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text("skills:\n  restricted:\n    bots: [bot_a]\n")
        reg = load_skills(tmp_skills, bot_id="bot_b", config_path=cfg_path)
        assert reg.get_skill("restricted") is None
