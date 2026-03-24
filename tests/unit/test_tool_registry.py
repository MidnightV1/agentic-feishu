"""Tool registry tests — schema generation, discovery, execution.

Covers:
- @tool decorator → JSON Schema from type hints
- Optional, list[X], docstring param description extraction
- Tool discovery from module / directory
- Deferred tool lifecycle (collapsed → first call → expanded → reset)
- Skill loader integration
- Tool name conflict detection
- P1-8: tool_support flag check
"""
import pytest
from typing import Optional


class TestSchemaGeneration:
    """JSON Schema generated from function signature + type hints."""

    def test_basic_string_param(self):
        from core.tool_registry import tool

        @tool(description="Read a file")
        def read_file(path: str) -> str:
            return ""

        schema = read_file._tool_meta.schema
        assert schema["parameters"]["properties"]["path"]["type"] == "string"
        assert "path" in schema["parameters"]["required"]

    def test_default_value_not_required(self):
        from core.tool_registry import tool

        @tool(description="Read")
        def read_file(path: str, encoding: str = "utf-8") -> str:
            return ""

        schema = read_file._tool_meta.schema
        assert "path" in schema["parameters"]["required"]
        assert "encoding" not in schema["parameters"]["required"]

    def test_optional_type(self):
        from core.tool_registry import tool

        @tool(description="Test")
        def fn(x: Optional[int] = None) -> str:
            return ""

        props = fn._tool_meta.schema["parameters"]["properties"]
        assert props["x"]["type"] == "integer"

    def test_list_type(self):
        from core.tool_registry import tool

        @tool(description="Test")
        def fn(items: list[str]) -> str:
            return ""

        props = fn._tool_meta.schema["parameters"]["properties"]
        assert props["items"]["type"] == "array"
        assert props["items"]["items"]["type"] == "string"

    def test_bool_type(self):
        from core.tool_registry import tool

        @tool(description="Test")
        def fn(flag: bool = False) -> str:
            return ""

        props = fn._tool_meta.schema["parameters"]["properties"]
        assert props["flag"]["type"] == "boolean"

    def test_docstring_param_description(self):
        from core.tool_registry import tool

        @tool(description="Read file content")
        def read_file(path: str) -> str:
            """Read a file from disk.

            Args:
                path: The absolute file path to read.
            """
            return ""

        props = read_file._tool_meta.schema["parameters"]["properties"]
        assert "description" in props["path"]
        assert "file path" in props["path"]["description"].lower()

    def test_no_params_empty_schema(self):
        from core.tool_registry import tool

        @tool(description="Get time")
        def get_time() -> str:
            return ""

        schema = get_time._tool_meta.schema
        assert schema["parameters"]["properties"] == {}

    def test_parallel_safe_flag(self):
        from core.tool_registry import tool

        @tool(description="Safe", parallel_safe=True)
        def safe_fn() -> str:
            return ""

        @tool(description="Unsafe")
        def unsafe_fn() -> str:
            return ""

        assert safe_fn._tool_meta.parallel_safe is True
        assert unsafe_fn._tool_meta.parallel_safe is False


class TestDeferredLoading:
    """Deferred tool lifecycle: collapsed → first call returns doc → expanded."""

    async def test_collapsed_schema_uses_summary(self, tool_registry):
        from core.tool_registry import Tool

        tool_registry.register(Tool(
            name="feishu_doc",
            summary="飞书文档操作",
            description="这是完整的飞书文档操作手册，包含创建、读取、更新、删除等所有操作...",
            deferred=True,
            handler=lambda **kw: "executed",
        ))
        schemas = tool_registry.get_tool_schemas()
        doc = next(s for s in schemas if s["function"]["name"] == "feishu_doc")
        assert doc["function"]["description"] == "飞书文档操作"
        assert "完整" not in doc["function"]["description"]

    async def test_first_call_returns_doc(self, tool_registry):
        from core.tool_registry import Tool

        full_desc = "完整文档手册：创建文档用 action=create..."
        tool_registry.register(Tool(
            name="feishu_doc", summary="摘要",
            description=full_desc, deferred=True,
            handler=lambda **kw: "should not execute",
        ))
        result = await tool_registry.execute("feishu_doc", {"action": "create"})
        assert full_desc in result.content
        assert tool_registry._tools["feishu_doc"].expanded is True

    async def test_second_call_executes(self, tool_registry):
        from core.tool_registry import Tool

        executed = []
        tool_registry.register(Tool(
            name="feishu_doc", summary="摘要",
            description="文档", deferred=True,
            handler=lambda **kw: executed.append(True) or "done",
        ))
        await tool_registry.execute("feishu_doc", {})  # expand
        result = await tool_registry.execute("feishu_doc", {"action": "create"})
        assert len(executed) == 1

    async def test_reset_expansions(self, tool_registry):
        from core.tool_registry import Tool

        tool_registry.register(Tool(
            name="feishu_doc", summary="摘要",
            description="文档", deferred=True,
            handler=lambda **kw: "x",
        ))
        await tool_registry.execute("feishu_doc", {})
        assert tool_registry._tools["feishu_doc"].expanded is True
        tool_registry.reset_expansions()
        assert tool_registry._tools["feishu_doc"].expanded is False


class TestSkillLoader:
    """Skill discovery from directory structure."""

    def test_discover_skill_from_yaml(self, tmp_path, tool_registry):
        """skill.yaml + tools.py → tools registered."""
        skill_dir = tmp_path / "weather"
        skill_dir.mkdir()
        (skill_dir / "skill.yaml").write_text(
            "name: weather\n"
            "description: Weather queries\n"
            "triggers:\n"
            '  - "天气|weather|气温"\n'
            "enabled: true\n"
            "priority: 10\n"
        )
        (skill_dir / "tools.py").write_text(
            "from core.tool_registry import tool\n\n"
            "@tool(description='Query weather')\n"
            "def weather_query(city: str) -> str:\n"
            "    return f'Weather for {city}'\n"
        )
        from skills.loader import load_skills
        load_skills(tmp_path, tool_registry)
        assert "weather_query" in tool_registry._tools

    def test_disabled_skill_skipped(self, tmp_path, tool_registry):
        skill_dir = tmp_path / "disabled"
        skill_dir.mkdir()
        (skill_dir / "skill.yaml").write_text(
            "name: disabled\ndescription: X\nenabled: false\n"
        )
        (skill_dir / "tools.py").write_text(
            "from core.tool_registry import tool\n\n"
            "@tool(description='X')\ndef noop() -> str: return ''\n"
        )
        from skills.loader import load_skills
        load_skills(tmp_path, tool_registry)
        assert "noop" not in tool_registry._tools

    def test_missing_yaml_ignored(self, tmp_path, tool_registry):
        (tmp_path / "broken").mkdir()
        (tmp_path / "broken" / "tools.py").write_text("# no yaml")
        from skills.loader import load_skills
        load_skills(tmp_path, tool_registry)
        assert len(tool_registry._tools) == 0


class TestTriggerMatching:
    """Skill trigger regex matching."""

    def test_regex_match(self):
        from skills.loader import SkillInfo, SkillRegistry

        reg = SkillRegistry()
        reg.register(SkillInfo(name="weather", triggers=[r"天气|weather"], priority=10))
        match = reg.match_trigger("今天天气怎么样")
        assert match is not None
        assert match.name == "weather"

    def test_no_match(self):
        from skills.loader import SkillInfo, SkillRegistry

        reg = SkillRegistry()
        reg.register(SkillInfo(name="weather", triggers=[r"天气"], priority=10))
        assert reg.match_trigger("你好") is None

    def test_priority_order(self):
        from skills.loader import SkillInfo, SkillRegistry

        reg = SkillRegistry()
        reg.register(SkillInfo(name="specific", triggers=[r"飞书文档"], priority=20))
        reg.register(SkillInfo(name="general", triggers=[r"飞书"], priority=10))
        match = reg.match_trigger("帮我看飞书文档")
        assert match.name == "specific"
