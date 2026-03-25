# agentic-feishu

Native agent framework for Feishu (Lark). Build intelligent bots with tool use, context management, and multi-model LLM support.

[中文文档](#中文文档)

## Highlights

- **Agent loop with tools**: Decorator-based tool registration, automatic JSON Schema generation, multi-turn tool execution
- **Native Feishu integration**: WebSocket event handling, card-based markdown rendering, document/task/calendar tools
- **Multi-model support**: OpenAI-compatible SDK verified with DeepSeek; 12+ Chinese & international provider presets ready. If you use [Claude Code](https://docs.anthropic.com/en/docs/claude-code), check out [claude-hub](https://github.com/MidnightV1/claude-hub) for a Claude Code + Feishu integration
- **Context management**: Priority-layered system prompt assembly, auto-compression at configurable thresholds
- **Session persistence**: SQLite for queries + JSONL backup for audit/debug
- **Multi-bot**: Run multiple Feishu bots with independent configs, personas, and providers from a single process
- **Skill system**: Same skill implementation as [Claude Code](https://docs.anthropic.com/en/docs/claude-code/skills) — `.claude/skills/<name>/SKILL.md` + scripts

## Architecture

```
User (Feishu)
    │
WebSocket Adapter ── Tag Protocol / Card Dispatcher / Media Handler
    │
Agent Loop ── Tool Registry / Context Manager / Session Store
    │
LLM Provider ── Presets (12+ providers)
```

Core modules:

| Module | Purpose |
|--------|---------|
| `core/agent_loop.py` | Main loop: messages → provider → tool calls → results |
| `core/context_assembler.py` | Multi-layer system prompt assembly with priority |
| `core/tool_registry.py` | `@tool` decorator, JSON Schema auto-generation |
| `platforms/feishu/adapter.py` | WebSocket handler, debounce, tag protocol |
| `platforms/feishu/dispatcher.py` | Card message dispatch, 4K chunking, secret redaction |
| `providers/` | Multi-model support with preset auto-resolution |
| `infra/session.py` | Async SQLite session store with bot isolation |

## Quick Start

**Prerequisites**: Python 3.12+, a Feishu app with Bot capability

### Installation

```bash
git clone https://github.com/MidnightV1/agentic-feishu.git
cd agentic-feishu
pip install -e .
```

### Configuration

```bash
cp config.yaml.example config.yaml
```

Minimal config:

```yaml
default_provider: deepseek

deepseek:
  api_key: "your-api-key"

feishu:
  app_id: "cli_xxxxx"
  app_secret: "your-secret"
```

All values support env var override: `AGENTIC_DEEPSEEK_API_KEY`, `AGENTIC_FEISHU_APP_ID`, etc.

### Run

```bash
python main.py
```

## Supported Providers

DeepSeek (OpenAI-compatible SDK) has been verified end-to-end. Other providers have presets ready — set the provider name and API key to use them.

| Provider | SDK | Default Model | Status |
|----------|-----|---------------|--------|
| DeepSeek | openai | deepseek-chat | **Verified** |
| OpenAI | openai | gpt-4.1 | Preset ready |
| Anthropic | anthropic | claude-sonnet-4-6 | Preset ready |
| Gemini | google-genai | gemini-2.5-flash | Preset ready |
| Kimi | openai | kimi-k2.5 | Preset ready |
| Qwen | openai | qwen3-max | Preset ready |
| Zhipu | openai | glm-5 | Preset ready |
| MiniMax | openai | MiniMax-M2.5 | Preset ready |
| Stepfun | openai | step-3.5-flash | Preset ready |
| Baichuan | openai | Baichuan4 | Preset ready |
| Yi | openai | yi-lightning | Preset ready |
| OpenRouter | openai | claude-sonnet-4 | Preset ready |

Provider presets auto-fill `base_url` and model defaults. Just set the provider name and API key.

## Feishu App Setup

1. Go to [Feishu Open Platform](https://open.feishu.cn/), Create App
2. Enable **Bot** capability
3. Add required scopes (see below)
4. Deploy with **WebSocket mode** (no public URL needed)
5. Add `app_id` and `app_secret` to `config.yaml`

### Required Scopes

**IM (Messaging)** — Required

| Scope | Description |
|-------|-------------|
| `im:message` | Send and receive messages |
| `im:message:send_as_bot` | Send messages as bot |
| `im:resource` | Download images and files |
| `im:chat` | Get chat info |

**Documents** — Optional, for document tools

| Scope | Description |
|-------|-------------|
| `docx:document` | Read and write documents |
| `docx:document:readonly` | Read documents |
| `docs:doc` | Legacy doc access |
| `drive:drive` | Cloud storage access |
| `drive:drive:readonly` | Read cloud storage |
| `wiki:wiki` | Wiki space access |
| `wiki:wiki:readonly` | Read wiki |
| `sheets:spreadsheet` | Spreadsheet access |

**Tasks** — Optional, for task tools

| Scope | Description |
|-------|-------------|
| `task:task` | Create and manage tasks |
| `task:task:readonly` | Read tasks |

**Calendar** — Optional, for calendar tools

| Scope | Description |
|-------|-------------|
| `calendar:calendar` | Read and write calendar |
| `calendar:calendar:readonly` | Read calendar |

**Bitable** — Optional, for multidimensional table tools

| Scope | Description |
|-------|-------------|
| `bitable:bitable` | Read and write bitable |
| `bitable:bitable:readonly` | Read bitable |

**Contact** — Optional, for contact resolution

| Scope | Description |
|-------|-------------|
| `contact:user.base:readonly` | Read basic user info |
| `contact:user.email:readonly` | Read user email |

## Tool Development

Register tools with the `@tool` decorator:

```python
from core.tool_registry import tool

@tool
async def search_web(query: str, max_results: int = 5) -> str:
    """Search the web for information.

    Args:
        query: Search query string
        max_results: Maximum number of results to return
    """
    # Your implementation here
    return results
```

The decorator automatically:
- Generates JSON Schema from type hints + docstring
- Registers the tool in the global registry
- Handles argument parsing and error wrapping

Place custom tools in `tools/custom/` — they are auto-discovered on startup.

## Context System

System prompts are assembled from priority-layered components:

| Priority | Component | Source |
|----------|-----------|--------|
| 105 | Platform rules | Rendering rules, tag protocol |
| 100 | Organization soul | Core principles |
| 97 | Tools + Skills | Available capabilities |
| 95 | Bot soul | Bot-specific personality |
| 80 | User persona | User-specific interaction style |
| 60 | User profile | User metadata |

Auto-compression triggers at 80% context window usage (configurable), using a separate provider if desired.

## Multi-Bot Setup

```yaml
bots:
  - name: assistant
    app_id: "cli_aaa"
    app_secret: "secret_aaa"
    provider: deepseek
    default_persona: default

  - name: analyst
    app_id: "cli_bbb"
    app_secret: "secret_bbb"
    provider: openai
    default_persona: analyst
```

Each bot gets isolated sessions, context, and provider configuration.

## Agent Configuration (CLAUDE.md)

This project supports AI agent collaboration via `CLAUDE.md`. When working with Claude Code or similar AI coding assistants:

**Setting up CLAUDE.md for your fork:**

1. Create a `CLAUDE.md` at the project root
2. Describe your project structure, conventions, and tool registration patterns
3. The agent will read this file automatically on session start

**Key conventions for agents:**

- **Tool registration**: Use the `@tool` decorator in `tools/` directory
- **Provider presets**: Add new providers in `providers/presets.py`
- **Platform adapters**: Follow the pattern in `platforms/feishu/`
- **Skills**: Create `SKILL.md` + `scripts/` under `.claude/skills/<name>/`
- **Config**: All configuration goes through `config.yaml` with env var overrides

## Project Structure

```
agentic-feishu/
  main.py                  # Entry point
  config.yaml.example      # Configuration template
  core/                    # Agent engine
    agent_loop.py          # Main execution loop
    context_assembler.py   # System prompt assembly
    context_manager.py     # Compression + fallback
    tool_registry.py       # @tool decorator + schema
    types.py               # Shared data models
    streaming.py           # Stream response handling
  providers/               # LLM providers
    factory.py             # Auto-resolution from presets
    base.py                # Provider interface
    openai_provider.py     # OpenAI + compatible providers
    anthropic_provider.py  # Anthropic Claude
    gemini_provider.py     # Google Gemini
    presets.py             # Provider registry (12+)
  platforms/feishu/        # Feishu integration
    adapter.py             # WebSocket event handler
    api.py                 # Async Feishu API client
    dispatcher.py          # Card message sender
    blocks.py              # Markdown → Feishu blocks
    media.py               # Image/file handler
    contacts.py            # Contact resolution
    tags.py                # XML tag protocol
  tools/                   # Tool definitions
    builtin/               # Built-in tools (file, bash, feishu)
    custom/                # Auto-discovered custom tools
  skills/                  # Pluggable skills (SKILL.md + scripts)
  infra/                   # State management
    session.py             # SQLite session store
    memory.py              # Long-term memory
    user_profile.py        # User profiles
    usage.py               # Token usage tracking
  jobs/                    # Background jobs
    scheduler.py           # In-process cron
    heartbeat.py           # Task monitoring
  templates/               # Prompt templates
    personas/              # Bot personality modes
    soul.md                # Core agent instructions
  config/                  # Settings
  data/                    # Runtime state (generated)
  workspace/               # User workspace (generated)
```

## License

MIT

---

# 中文文档

面向飞书（Lark）的原生 Agent 框架。支持工具调用、上下文管理和多模型 LLM。

## 特性

- **Agent 循环 + 工具调用**：基于装饰器的工具注册，自动生成 JSON Schema，多轮工具执行
- **原生飞书集成**：WebSocket 事件处理，卡片 Markdown 渲染，文档/任务/日历工具
- **多模型支持**：已验证 DeepSeek（OpenAI 兼容 SDK）；12+ 国内外模型预设就绪。如果你使用 [Claude Code](https://docs.anthropic.com/en/docs/claude-code)，可以看看 [claude-hub](https://github.com/MidnightV1/claude-hub) 的 Claude Code + 飞书集成方案
- **上下文管理**：优先级分层的系统提示词组装，可配置阈值的自动压缩
- **会话持久化**：SQLite 查询 + JSONL 审计备份
- **多 Bot**：单进程运行多个飞书 Bot，独立配置、人设和模型
- **Skill 系统**：与 [Claude Code](https://docs.anthropic.com/en/docs/claude-code/skills) 相同的实现方案 — `.claude/skills/<name>/SKILL.md` + scripts

## 快速开始

**前置条件**：Python 3.12+，飞书应用（开启 Bot 能力）

```bash
git clone https://github.com/MidnightV1/agentic-feishu.git
cd agentic-feishu
pip install -e .
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入 API key 和飞书凭据
python main.py
```

最小配置：

```yaml
default_provider: deepseek

deepseek:
  api_key: "你的 API key"

feishu:
  app_id: "cli_xxxxx"
  app_secret: "你的 app secret"
```

所有配置项支持环境变量覆盖：`AGENTIC_DEEPSEEK_API_KEY`、`AGENTIC_FEISHU_APP_ID` 等。

## 飞书应用配置

1. 前往[飞书开放平台](https://open.feishu.cn/)创建应用
2. 开启 **机器人** 能力
3. 添加所需权限（见上方英文文档的 Required Scopes 部分）
4. 使用 **WebSocket 模式** 部署（无需公网 URL）
5. 将 `app_id` 和 `app_secret` 填入 `config.yaml`

## 工具开发

使用 `@tool` 装饰器注册工具：

```python
from core.tool_registry import tool

@tool
async def search_web(query: str, max_results: int = 5) -> str:
    """搜索网络信息。

    Args:
        query: 搜索关键词
        max_results: 最大返回结果数
    """
    return results
```

装饰器自动完成：
- 从类型标注 + docstring 生成 JSON Schema
- 注册到全局工具注册表
- 处理参数解析和错误包装

自定义工具放在 `tools/custom/` 目录，启动时自动发现。

## Agent 协作配置

项目通过 `CLAUDE.md` 支持 AI Agent 协作。与 Claude Code 或类似 AI 编程助手配合时：

- 在项目根目录创建 `CLAUDE.md`，描述项目结构和约定
- **工具注册**：在 `tools/` 目录使用 `@tool` 装饰器
- **模型预设**：在 `providers/presets.py` 添加新模型
- **平台适配**：参照 `platforms/feishu/` 的模式
- **Skill**：在 `.claude/skills/<name>/` 下创建 `SKILL.md` + `scripts/`

## 许可证

MIT
