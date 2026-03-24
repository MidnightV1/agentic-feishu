"""Core pytest fixtures for agentic-feishu tests.

Design principles:
- All external dependencies (Feishu API, LLM providers) are mocked
- Each test gets isolated tmp_path for storage
- Golden test framework: --update-golden to regenerate snapshots
- Fixtures are layered: primitives → composed → specialized
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── CLI options ──────────────────────────────────────────

def pytest_addoption(parser):
    parser.addoption(
        "--update-golden", action="store_true", default=False,
        help="Regenerate golden files instead of comparing",
    )
    parser.addoption(
        "--llm-runs", type=int, default=1,
        help="Repeat LLM-dependent tests N times for variance detection",
    )


# ── Basic fixtures ───────────────────────────────────────

@pytest.fixture
def update_golden(request):
    return request.config.getoption("--update-golden")


@pytest.fixture
def llm_runs(request):
    return request.config.getoption("--llm-runs")


@pytest.fixture
def config():
    """Minimal hub config with no real credentials."""
    return {
        "feishu": {
            "bots": [{
                "name": "test",
                "app_id": "cli_test",
                "app_secret": "test_secret",
                "admin_open_ids": ["ou_admin"],
            }],
        },
        "llm": {
            "default_provider": "anthropic",
            "default_model": "sonnet",
            "providers": {
                "anthropic": {"api_key": "sk-test"},
                "openai": {"api_key": "sk-test"},
                "gemini": {"api_key": "test"},
            },
        },
        "scheduler": {"enabled": False},
        "heartbeat": {"enabled": False},
    }


# ── Mock providers ───────────────────────────────────────

@pytest.fixture
def mock_provider():
    """Returns a mock provider with preset successful response."""
    from core.types import Message, Usage

    provider = AsyncMock()
    provider.chat = AsyncMock(return_value=Message(
        role="assistant", content="mock response",
    ))
    provider._last_usage = Usage()
    provider._last_message = None
    provider.count_tokens = AsyncMock(return_value=100)
    provider.model_id = "test-model"
    return provider


@pytest.fixture
def mock_provider_with_tools():
    """Mock provider that returns tool calls on first call, text on second."""
    from core.types import Message, ToolCall

    responses = iter([
        Message(role="assistant", content="", tool_calls=[
            ToolCall(id="tc_1", name="echo", arguments={"text": "hello"}),
        ]),
        Message(role="assistant", content="Done: hello"),
    ])
    provider = AsyncMock()
    provider.chat = AsyncMock(side_effect=lambda **kw: next(responses))
    provider.count_tokens = AsyncMock(return_value=100)
    provider.model_id = "test-model"
    return provider


# ── Storage fixtures ─────────────────────────────────────

@pytest.fixture
async def session_store(tmp_path):
    """Isolated session store with tmp database."""
    from infra.session import SessionStore

    store = SessionStore(str(tmp_path / "test_sessions.db"))
    await store.init()
    yield store
    await store.close()


# ── Adapter fixtures ─────────────────────────────────────

@pytest.fixture
def make_event():
    """Factory for Feishu message events."""
    _counter = 0

    def _make(
        msg_id=None, text="hello", msg_type="text",
        chat_type="p2p", sender_id="ou_user1",
        chat_id="oc_chat1", mentions=None,
        create_time=None, image_key=None, file_key=None,
    ):
        nonlocal _counter
        _counter += 1
        if msg_id is None:
            msg_id = f"om_test_{_counter}"
        if create_time is None:
            create_time = str(int(time.time() * 1000))

        content = {}
        if msg_type == "text":
            content = {"text": text}
        elif msg_type == "image":
            content = {"image_key": image_key or f"img_{_counter}"}
        elif msg_type == "file":
            content = {"file_key": file_key or f"file_{_counter}",
                       "file_name": "test.txt"}

        return MagicMock(
            event=MagicMock(
                message=MagicMock(
                    message_id=msg_id,
                    message_type=msg_type,
                    content=json.dumps(content),
                    chat_id=chat_id,
                    chat_type=chat_type,
                    create_time=create_time,
                    mentions=mentions or [],
                ),
                sender=MagicMock(
                    sender_id=MagicMock(open_id=sender_id),
                ),
            ),
        )

    return _make


# ── Tool fixtures ────────────────────────────────────────

@pytest.fixture
def tool_registry():
    """Fresh tool registry."""
    from core.tool_registry import ToolRegistry

    return ToolRegistry()


@pytest.fixture
def echo_tool():
    """A simple echo tool for testing."""
    from core.tool_registry import tool

    @tool(description="Echo text back", parallel_safe=True)
    def echo(text: str) -> str:
        """Echo the input text.

        Args:
            text: Text to echo back.
        """
        return text

    return echo


@pytest.fixture
def failing_tool():
    """A tool that always raises an error."""
    from core.tool_registry import tool

    @tool(description="Always fails")
    def fail_tool() -> str:
        raise RuntimeError("Intentional failure for testing")

    return fail_tool


# ── Golden test utilities ────────────────────────────────

GOLDEN_DIR = Path(__file__).parent / "golden"


def assert_golden(rel_path: str, actual: str, update: bool):
    """Compare actual output against golden file.

    If update=True (--update-golden), writes actual to golden file.
    Otherwise, asserts exact match.
    """
    golden_path = GOLDEN_DIR / rel_path
    if update:
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(actual, encoding="utf-8")
        return
    assert golden_path.exists(), f"Golden file missing: {rel_path}. Run with --update-golden to create."
    expected = golden_path.read_text(encoding="utf-8")
    if actual != expected:
        # Show first difference for debugging
        for i, (a, e) in enumerate(zip(actual, expected)):
            if a != e:
                ctx = 40
                pytest.fail(
                    f"Golden mismatch in {rel_path} at char {i}:\n"
                    f"  expected: ...{expected[max(0,i-ctx):i+ctx]}...\n"
                    f"  actual:   ...{actual[max(0,i-ctx):i+ctx]}..."
                )
        if len(actual) != len(expected):
            pytest.fail(
                f"Golden mismatch in {rel_path}: "
                f"length {len(actual)} vs expected {len(expected)}"
            )


def load_fixture(rel_path: str) -> dict | list | str:
    """Load a test fixture file."""
    fixture_path = Path(__file__).parent / "fixtures" / rel_path
    text = fixture_path.read_text(encoding="utf-8")
    if fixture_path.suffix == ".json":
        return json.loads(text)
    return text
