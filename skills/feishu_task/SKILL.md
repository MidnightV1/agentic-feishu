---
description: Track commitments and follow-ups as Feishu tasks (任务/待办/承诺追踪). Use when the user mentions tasks (任务), to-do items (待办/todo), deadlines (截止日期/ddl), task assignments (指派), or makes implicit commitments like "记得提醒我..."、"别忘了..."、"下周之前要...". Also triggered by discussion action items, bot-discovered follow-ups (code TODOs, failed jobs), and async handoffs ("帮我查一下X，周五前给我"). Task answers "什么事要记得做完，截止时间是什么" — stateful items that need completion tracking.
---

# Feishu Tasks

把对话中的承诺固化为可追踪状态。聊天里说的"下周搞一下"很容易被淹没，但变成一条有 deadline 的任务就不会。

**触发场景**（不只是用户显式说"建个任务"）：
1. **隐式承诺** — 用户说"记得提醒我..."、"别忘了..."、"下周之前要..."
2. **讨论后的 action items** — 文档/方案讨论结束后，结论中的待办事项
3. **Bot 发现的待跟进项** — 审查代码发现 TODO、定时任务失败需要处理
4. **异步交接** — "帮我查一下X，周五前给我" → 给自己建任务，到期交付

**与日历的边界**：日历管理时间锚点（有开始/结束、需要"在场"的事件），任务管理承诺追踪（有状态流转、需要"做完"的事项）。

## Actions

调用方式：`feishu_task(action, params)`

- **create** — 创建任务。params: `{title, due_date?, description?}`。`due_date` 支持多种格式（见下方 Due Date Formats）。
- **get** — 获取任务详情。params: `{task_id}`。
- **list** — 列出任务。params: `{completed?}`。默认返回未完成任务；传 `completed=true` 返回已完成任务。
- **update** — 更新任务字段。params: `{task_id, title?, due_date?, description?}`。只传需要修改的字段。
- **complete** — 标记任务完成。params: `{task_id}`。
- **delete** — 删除任务（两阶段确认）。第一次调用（不带 `confirmed`）返回确认提示；第二次调用加 `confirmed=true` 才执行删除。params: `{task_id, confirmed?}`。
- **assign** — 指派用户。params: `{task_id, open_ids}`。`open_ids` **必须为逗号分隔字符串**，不能传 list（如 `"ou_aaa,ou_bbb"`）。
- **unassign** — 取消指派。params: `{task_id, open_ids}`。同上，CSV 字符串。
- **snapshot** — 获取所有未完成任务的分类概览（逾期 / 即将到期 / 进行中）。无需参数。无未完成任务时静默返回，不产生输出。

## Due Date Formats

| 格式 | 示例 | 说明 |
|------|------|------|
| ISO datetime | `2026-03-10T15:00` 或 `2026-03-10 15:00` | 完整日期时间 |
| ISO date only | `2026-03-10` | 当天午夜 |
| Time only | `15:00` | 今天该时刻；若已过则顺延至明天 |
| Relative | `+2h`、`+30m` | 从当前时刻起偏移 |
| Natural | `tomorrow 15:00`、`tomorrow` | 自然语言，代码自动解析 |

## Coded Behaviors

代码保证的行为，无需 LLM 判断：

- **CSV auto-parse** — `assign`/`unassign` 的 `open_ids` 只接受字符串，代码内部自动按逗号拆分。**禁止传 list**，否则类型错误。
- **Confirmation-gated deletion** — `delete` 不带 `confirmed` 时返回「确认删除？」提示，不执行任何写操作；加 `confirmed=true` 后才真正删除。
- **Snapshot silent exit** — `snapshot` 检测到无未完成任务时静默返回空结果，不向用户输出任何内容，减少心跳噪声。
- **Due date auto-parse** — `create`/`update` 的 `due_date` 字段自动处理 ISO、自然语言、相对偏移和飞书毫秒时间戳格式。

## Params Examples

```python
# 创建任务，自然语言 due_date
feishu_task("create", {"title": "提交周报", "due_date": "tomorrow 17:00", "description": "Q1 summary"})

# 列出未完成任务（默认）
feishu_task("list", {})

# 列出已完成任务
feishu_task("list", {"completed": true})

# 指派多人，open_ids 必须是 CSV 字符串
feishu_task("assign", {"task_id": "xxx", "open_ids": "ou_aaa,ou_bbb"})

# 标记完成
feishu_task("complete", {"task_id": "xxx"})

# 删除（两阶段：第一次无 confirmed → 提示；第二次加 confirmed=true → 执行）
feishu_task("delete", {"task_id": "xxx", "confirmed": true})

# 任务概览（心跳/定期检查用）
feishu_task("snapshot", {})
```
