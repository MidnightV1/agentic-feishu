"""Tool registry for agentic-feishu: decorator-based registration, schema generation, execution."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, get_type_hints

from .types import ToolResult

logger = logging.getLogger(__name__)

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict          # JSON Schema for parameters
    handler: Callable         # async or sync function
    parallel_safe: bool = False
    timeout: float = 30.0


# ── Type mapping ──────────────────────────────────────────────────────────────

_TYPE_MAP: dict[type, dict] = {
    str:   {"type": "string"},
    int:   {"type": "integer"},
    float: {"type": "number"},
    bool:  {"type": "boolean"},
    list:  {"type": "array"},
    dict:  {"type": "object"},
}


def _type_to_schema(tp: type) -> dict:
    """Convert a Python type annotation to a JSON Schema fragment."""
    origin = getattr(tp, "__origin__", None)

    # list[X] / dict[K, V]
    if origin is list:
        args = getattr(tp, "__args__", None)
        schema: dict[str, Any] = {"type": "array"}
        if args:
            schema["items"] = _type_to_schema(args[0])
        return schema
    if origin is dict:
        return {"type": "object"}

    return dict(_TYPE_MAP.get(tp, {"type": "string"}))


def _is_optional(tp: type) -> tuple[bool, type]:
    """Return (True, inner_type) if tp is Optional[X], else (False, tp)."""
    import types as _types
    args = getattr(tp, "__args__", None)
    # Python 3.10+ union: X | None
    if isinstance(tp, _types.UnionType):
        if args and len(args) == 2 and type(None) in args:
            inner = args[0] if args[1] is type(None) else args[1]
            return True, inner
    # typing.Union[X, None]
    origin = getattr(tp, "__origin__", None)
    try:
        import typing
        if origin is typing.Union and args and len(args) == 2 and type(None) in args:
            inner = args[0] if args[1] is type(None) else args[1]
            return True, inner
    except Exception:
        pass
    return False, tp


# ── Docstring parsing ─────────────────────────────────────────────────────────

_GOOGLE_PARAM_RE = re.compile(
    r"^\s{2,}(\w+)\s*(?:\(.+?\))?\s*:\s*(.+)", re.MULTILINE
)

def _parse_param_descriptions(docstring: str | None) -> dict[str, str]:
    """Extract parameter descriptions from Google-style or numpy-style docstrings."""
    if not docstring:
        return {}
    descs: dict[str, str] = {}
    for m in _GOOGLE_PARAM_RE.finditer(docstring):
        descs[m.group(1)] = m.group(2).strip()
    return descs


# ── Schema generation ─────────────────────────────────────────────────────────

def _build_parameters_schema(func: Callable) -> dict:
    """Build a JSON Schema 'parameters' object from function signature + type hints."""
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    param_docs = _parse_param_descriptions(func.__doc__)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        tp = hints.get(name, str)
        optional, tp = _is_optional(tp)

        # Skip return annotation leak
        if name == "return":
            continue

        schema = _type_to_schema(tp)
        if name in param_docs:
            schema["description"] = param_docs[name]

        properties[name] = schema

        has_default = param.default is not inspect.Parameter.empty
        if not has_default and not optional:
            required.append(name)

    result: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


# ── @tool decorator ───────────────────────────────────────────────────────────

def tool(
    name: str = "",
    description: str = "",
    parallel_safe: bool = False,
    timeout: float = 30.0,
) -> Callable:
    """Decorator to register a function as an agent tool.

    Usage:
        @tool("create_task", "Create a Feishu task")
        async def create_task(title: str, due: str = "") -> dict:
            ...

        @tool(parallel_safe=True)
        async def search(query: str) -> str:
            ...
    """

    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        tool_desc = description
        if not tool_desc:
            doc = func.__doc__
            tool_desc = doc.strip().split("\n")[0] if doc else tool_name

        func._tool_meta = {  # noqa: SLF001
            "name": tool_name,
            "description": tool_desc,
            "parameters": _build_parameters_schema(func),
            "parallel_safe": parallel_safe,
            "timeout": timeout,
        }

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

        wrapper._tool_meta = func._tool_meta  # noqa: SLF001
        return wrapper

    return decorator


# ── ToolRegistry ──────────────────────────────────────────────────────────────

class ToolRegistry:
    """Registry for agent tools. Supports decorator-based and manual registration."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # -- Registration ----------------------------------------------------------

    def register(self, t: Tool) -> None:
        """Register a tool manually."""
        if t.name in self._tools:
            logger.warning("Overwriting tool '%s'", t.name)
        self._tools[t.name] = t
        logger.debug("Registered tool '%s'", t.name)

    def register_decorated(self, func: Callable) -> None:
        """Register a function decorated with @tool."""
        meta = getattr(func, "_tool_meta", None)
        if meta is None:
            raise ValueError(f"{func} is not decorated with @tool")
        self.register(Tool(
            name=meta["name"],
            description=meta["description"],
            parameters=meta["parameters"],
            handler=func,
            parallel_safe=meta["parallel_safe"],
            timeout=meta["timeout"],
        ))

    def discover(self, module: Any) -> int:
        """Scan a module for @tool decorated functions and register them. Returns count."""
        count = 0
        for attr_name in dir(module):
            obj = getattr(module, attr_name, None)
            if callable(obj) and hasattr(obj, "_tool_meta"):
                self.register_decorated(obj)
                count += 1
        return count

    def discover_directory(self, directory: str | Path) -> int:
        """Auto-discover @tool functions from all .py files in a directory.

        Returns total count of registered tools.
        """
        import importlib.util
        from pathlib import Path as P

        d = P(directory)
        if not d.is_dir():
            return 0

        total = 0
        for py_file in sorted(d.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"custom_tools_{py_file.stem}", str(py_file)
                )
                if not spec or not spec.loader:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                n = self.discover(mod)
                if n:
                    logger.info("Discovered %d tools from %s", n, py_file.name)
                    total += n
            except Exception:
                logger.exception("Failed to load tools from %s", py_file)
        return total

    # -- Lookup ----------------------------------------------------------------

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools)

    def remove(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    # -- Schema export ---------------------------------------------------------

    def get_tool_schemas(self) -> list[dict]:
        """Get all tool schemas in OpenAI function-calling format."""
        schemas: list[dict] = []
        for t in self._tools.values():
            schemas.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            })
        return schemas

    # -- Execution -------------------------------------------------------------

    async def execute(
        self,
        name: str,
        arguments: dict,
        timeout: float | None = None,
    ) -> ToolResult:
        """Execute a tool by name with given arguments. Handles timeout and errors."""
        call_id = uuid.uuid4().hex[:12]
        t = self._tools.get(name)
        if t is None:
            return ToolResult(call_id, f"Error: unknown tool '{name}'", is_error=True)

        effective_timeout = timeout if timeout is not None else t.timeout

        try:
            coro: Any
            if asyncio.iscoroutinefunction(t.handler):
                coro = t.handler(**arguments)
            else:
                loop = asyncio.get_running_loop()
                coro = loop.run_in_executor(None, lambda: t.handler(**arguments))

            result = await asyncio.wait_for(coro, timeout=effective_timeout)

        except asyncio.TimeoutError:
            logger.warning("Tool '%s' timed out after %ss", name, effective_timeout)
            return ToolResult(call_id, f"Tool timed out after {effective_timeout}s", is_error=True)
        except Exception as exc:
            logger.exception("Tool '%s' raised %s", name, type(exc).__name__)
            return ToolResult(call_id, f"Error: {type(exc).__name__}: {exc}", is_error=True)

        # Serialize result
        if isinstance(result, (dict, list)):
            content = json.dumps(result, ensure_ascii=False, default=str)
        else:
            content = str(result) if result is not None else ""

        return ToolResult(call_id, content)
