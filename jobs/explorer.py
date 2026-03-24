# -*- coding: utf-8 -*-
"""Explore loop — queue management + autonomous execution engine.

Pipeline:
1. Receive explore hints (from <next-explore> tag) or manual queue additions
2. Queue for execution with priority ordering
3. ExplorerEngine: deep, time-boxed, goal-driven exploration sessions
4. Results update Goal Tree, write to Memory, notify via Feishu card

Execution model:
- Each task gets up to TASK_TIME_BUDGET (1h) with a dedicated LLM provider
- Session budget caps total wall-clock time (3h)
- Model selection: P0/P1 or design keywords -> strong model, else default
- Structured output: conclusion, findings, recommendations, goal tree updates
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

from core.types import Message

log = logging.getLogger("agentic.jobs.explorer")


# -- Data classes ----------------------------------------------------------

@dataclass
class ExploreItem:
    """A queued exploration item."""
    direction: str
    value: str
    priority: str = "P3"    # P1-P4
    status: str = "queued"  # queued | running | done | skipped
    source: str = "llm"     # llm | user | cron
    goal_id: str = ""       # linked Goal Tree goal ID
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0
    result_summary: str = ""


@dataclass
class ExploreResult:
    """Result of a single exploration task execution."""
    item: ExploreItem
    text: str = ""
    duration_s: float = 0
    model_used: str = ""
    success: bool = False
    error: str = ""


# -- Queue -----------------------------------------------------------------

class ExploreQueue:
    """Manages exploration items with persistence.

    Usage:
        queue = ExploreQueue("data/explore.jsonl")
        queue.add("模型能力矩阵设计", "统一降级方案", priority="P2")
        item = queue.next()
        queue.complete(item, "设计完成，见文档 xxx")
    """

    def __init__(self, log_path: str | Path = "data/explore.jsonl"):
        self._path = Path(log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._items: list[ExploreItem] = []
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            for line in self._path.read_text().splitlines():
                if line.strip():
                    self._items.append(ExploreItem(**json.loads(line)))
        except Exception as e:
            log.warning("Failed to load explore queue: %s", e)

    def _save(self) -> None:
        """Rewrite entire file (items are small, this is fine)."""
        try:
            with open(self._path, "w") as f:
                for item in self._items:
                    f.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
        except Exception as e:
            log.error("Failed to save explore queue: %s", e)

    def add(
        self,
        direction: str,
        value: str,
        priority: str = "P3",
        source: str = "llm",
        goal_id: str = "",
    ) -> ExploreItem:
        """Add an exploration item to the queue."""
        item = ExploreItem(
            direction=direction,
            value=value,
            priority=priority,
            source=source,
            goal_id=goal_id,
        )
        self._items.append(item)
        self._save()
        log.info("Explore queued [%s]: %s", priority, direction[:60])
        return item

    def add_from_hints(self, hints_text: str) -> int:
        """Parse <next-explore> content and add items.

        Expected format:
        - [方向] description
          [价值] value description
        """
        count = 0
        direction = ""
        for line in hints_text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("- [方向]") or line.startswith("-[方向]"):
                direction = line.split("]", 1)[-1].strip()
            elif line.startswith("[价值]") or line.startswith("  [价值]"):
                value = line.split("]", 1)[-1].strip()
                if direction:
                    self.add(direction, value)
                    count += 1
                    direction = ""
        return count

    def next(self) -> ExploreItem | None:
        """Get the highest-priority queued item."""
        queued = [i for i in self._items if i.status == "queued"]
        if not queued:
            return None
        # Sort by priority (P1 first)
        queued.sort(key=lambda x: x.priority)
        return queued[0]

    def complete(self, item: ExploreItem, summary: str) -> None:
        """Mark an item as completed with a result summary."""
        item.status = "done"
        item.completed_at = time.time()
        item.result_summary = summary
        self._save()

    def skip(self, item: ExploreItem, reason: str = "") -> None:
        item.status = "skipped"
        item.result_summary = reason
        self._save()

    def list_items(self, status: str = "") -> list[ExploreItem]:
        if status:
            return [i for i in self._items if i.status == status]
        return list(self._items)

    def stats(self) -> dict:
        return {
            "total": len(self._items),
            "queued": sum(1 for i in self._items if i.status == "queued"),
            "done": sum(1 for i in self._items if i.status == "done"),
            "skipped": sum(1 for i in self._items if i.status == "skipped"),
        }


# -- System Prompt ---------------------------------------------------------

EXPLORE_SYSTEM = """\
你是一个深度探索 agent，在用户休息时自主调研系统进化方向。

## 你的使命
{mission}

## 当前目标树 (OKR)
{goal_tree}

## 已有探索记录（避免重复）
{exploration_map}

## 工作方法

你不是搜索引擎——你是研究员。每个探索任务都要经过：

1. **现状摸底**（必须）：读代码、读文档、读 git log，理解真实状态。不凭猜测。
2. **矛盾识别**：找到关键矛盾——解决它能带动全局的那个问题。
3. **外部参考**（如适用）：用工具搜索最佳实践、类似项目的做法。
4. **方案设计**：给出具体、可操作的结论。不要模糊的"建议优化"。
5. **ROI 评估**：实施成本 vs 预期收益，值不值得做。

## 输出要求

探索结束时，输出以下结构化结论：

```
## 结论
一句话核心发现。

## 关键发现
1. [发现1] — 证据/数据支撑
2. [发现2] — ...

## 推荐行动
- [行动1] — 预期收益 / 实施成本
- [行动2] — ...

## 目标树更新
- goal_id: [对应的目标ID]
- question_answered: [回答了哪个子问题]
- new_findings: [新发现的摘要]
- new_questions: [探索中发现的新问题]

## 后续方向
- [值得继续探索的方向1]
- [值得继续探索的方向2]

## Memory建议
如果探索发现了值得记住的内容（新的外部引用、工具URL、技术事实、用户行为模式等），\
在这个章节列出。系统会自动提取并写入Memory。
- 事实类（API端点、版本号、URL）会即时写入
- 模式类（用户偏好、系统行为模式）会标记为待确认，积累多个信号后再正式写入
```

## 约束
- 你有充足的时间（最多1小时），请充分调研，不要急于下结论
- 宁可说"调研后发现这个方向价值不大，原因是..."也不要给出没有深度的结论
- 如果发现了小 bug 或明显可改进的代码（L1 级别），可以直接修复并 commit
- 修复时 commit message 必须以 [L1-auto] 开头
"""


# -- Explorer Engine -------------------------------------------------------

class ExplorerEngine:
    """Autonomous exploration execution engine.

    Drives deep, time-boxed exploration sessions:
    - Picks tasks from ExploreQueue by priority
    - Calls LLM provider with rich Goal Tree context
    - Updates Goal Tree and Memory from structured output
    - Notifies user via Feishu card
    """

    TASK_TIME_BUDGET = 3600       # 1 hour per task
    SESSION_TIME_BUDGET = 10800   # 3 hours per session
    MAX_TASKS_PER_SESSION = 5

    # Model selection: strong for P0/P1 and design tasks
    STRONG_MODEL = "claude-opus-4-6"
    DEFAULT_MODEL = "claude-sonnet-4-6"

    # Keywords that trigger strong model selection
    _DESIGN_KEYWORDS = re.compile(
        r"设计|架构|方案|评估|分析|策略|规划|进化"
    )

    def __init__(
        self,
        queue: ExploreQueue,
        goal_tree: Any,               # GoalTree instance
        memory_store: Any | None = None,     # MemoryStore instance
        provider_factory: Callable[[str], Any] | None = None,
        notify_fn: Callable[..., Coroutine] | None = None,
        notify_open_id: str = "",
    ):
        self._queue = queue
        self._goal_tree = goal_tree
        self._memory = memory_store
        self._provider_factory = provider_factory
        self._notify = notify_fn
        self._notify_open_id = notify_open_id
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def run_session(self) -> list[ExploreResult]:
        """Run an exploration session: pick tasks, execute, update state."""
        if self._is_running:
            log.warning("Explorer session already running, skipping")
            return []

        self._is_running = True
        session_start = time.time()
        results: list[ExploreResult] = []

        try:
            # Load goal tree for context
            self._goal_tree.load()

            # Build exploration map from completed items (avoid duplication)
            exploration_map = self._build_exploration_map()

            for _ in range(self.MAX_TASKS_PER_SESSION):
                # Check session time budget (5 min safety margin)
                if time.time() - session_start > self.SESSION_TIME_BUDGET - 300:
                    log.info("Session time budget reached after %d tasks",
                             len(results))
                    break

                task = self._queue.next()
                if not task:
                    log.info("Exploration queue empty")
                    break

                result = await self._execute_task(task, exploration_map)
                results.append(result)

                if result.success:
                    # Update exploration map for subsequent tasks
                    exploration_map.append({
                        "title": task.direction,
                        "summary": result.text[:200],
                    })

            elapsed = int(time.time() - session_start)
            done = sum(1 for r in results if r.success)
            summary = (
                f"Explorer session: {done}/{len(results)} tasks "
                f"in {elapsed}s"
            )
            log.info(summary)

            # Send session summary notification
            if results and self._notify and self._notify_open_id:
                await self._send_session_summary(results, elapsed)

            return results

        except Exception as e:
            log.exception("Explorer session failed: %s", e)
            return results
        finally:
            self._is_running = False

    async def _execute_task(
        self,
        task: ExploreItem,
        exploration_map: list[dict],
    ) -> ExploreResult:
        """Execute a single deep exploration task."""
        log.info("Exploring: [%s] %s", task.priority, task.direction)
        task.status = "running"
        self._queue._save()

        task_start = time.time()
        result = ExploreResult(item=task)

        try:
            model = self._pick_model(task)
            result.model_used = model

            if not self._provider_factory:
                raise RuntimeError("No provider_factory configured")

            provider = self._provider_factory("anthropic")

            # Build system prompt with goal tree context
            system_prompt = self._build_system_prompt(exploration_map)
            user_prompt = self._build_user_prompt(task)

            messages = [
                Message(role="system", content=system_prompt),
                Message(role="user", content=user_prompt),
            ]

            response = await provider.chat(
                messages=messages,
                stream=False,
                model=model,
                max_tokens=8192,
            )

            response_text = (
                response.text
                if hasattr(response, "text")
                else str(response.content)
            )

            duration = time.time() - task_start
            result.text = response_text[:8000]
            result.duration_s = round(duration, 1)
            result.success = True

            # Update Goal Tree from structured output
            self._update_goal_tree(response_text, task)

            # Mark complete
            self._queue.complete(task, result.text[:2000])

            # Write memory-worthy content
            await self._maybe_update_memory(task, response_text)

            log.info("Exploration done: %s (%.0fs, model=%s)",
                     task.direction, duration, model)

            # Per-task notification
            if self._notify and self._notify_open_id:
                await self._send_task_card(task, result)

        except Exception as e:
            duration = time.time() - task_start
            result.duration_s = round(duration, 1)
            result.error = str(e)
            log.error("Exploration failed: %s -- %s", task.direction, e)

            # Revert to queued for retry
            task.status = "queued"
            task.result_summary = f"Error after {int(duration)}s: {str(e)[:200]}"
            self._queue._save()

        return result

    def _pick_model(self, task: ExploreItem) -> str:
        """P0/P1 -> strong model, design keywords -> strong, else default."""
        if task.priority in ("P0", "P1"):
            return self.STRONG_MODEL
        if self._DESIGN_KEYWORDS.search(task.direction):
            return self.STRONG_MODEL
        return self.DEFAULT_MODEL

    def _build_system_prompt(self, exploration_map: list[dict]) -> str:
        """Build system prompt with Goal Tree and exploration map."""
        tree_text = self._goal_tree.format_for_prompt()
        mission = self._goal_tree._data.get("mission", "")

        map_lines = []
        for entry in exploration_map[-20:]:
            map_lines.append(
                f"- {entry.get('title', '?')}: "
                f"{entry.get('summary', '')[:100]}"
            )
        map_text = "\n".join(map_lines) if map_lines else "(无近期探索记录)"

        return EXPLORE_SYSTEM.format(
            mission=mission,
            goal_tree=tree_text,
            exploration_map=map_text,
        )

    def _build_user_prompt(self, task: ExploreItem) -> str:
        """Build the exploration prompt for a task."""
        parts = [f"# 探索任务: {task.direction}"]

        if task.value:
            parts.append(f"\n**价值**: {task.value}")

        if task.source:
            parts.append(f"**来源**: {task.source}")

        # Find related goal in tree
        if task.goal_id:
            for goal in self._goal_tree._data.get("goals", []):
                if goal.get("id") == task.goal_id:
                    parts.append(
                        f"\n## 关联目标: "
                        f"[{goal.get('priority', 'P2')}] {goal['title']}"
                    )
                    parts.append(f"*Why*: {goal.get('why', '')}")
                    open_qs = [
                        sq for sq in goal.get("sub_questions", [])
                        if sq.get("status") == "open"
                    ]
                    if open_qs:
                        parts.append("**待回答的问题**:")
                        for sq in open_qs[:3]:
                            parts.append(f"- {sq['q']}")
                    break

        parts.append(
            "\n## 要求\n"
            "深入调研这个主题。你有充足时间，请：\n"
            "1. 先读代码和文档理解现状\n"
            "2. 识别关键矛盾\n"
            "3. 如需要，搜索外部最佳实践\n"
            "4. 给出具体结论和推荐行动\n"
            "5. 按系统提示中的结论格式输出"
        )

        return "\n".join(parts)

    def _update_goal_tree(self, result_text: str, task: ExploreItem) -> None:
        """Extract goal tree updates from structured exploration output."""
        if "目标树更新" not in result_text:
            return

        try:
            goal_match = re.search(r'goal_id:\s*(G\d+)', result_text)
            findings_match = re.search(
                r'new_findings:\s*(.+?)(?:\n##|\n-\s*new_questions|\Z)',
                result_text, re.DOTALL,
            )
            question_match = re.search(
                r'question_answered:\s*(.+?)(?:\n|\Z)', result_text,
            )

            goal_id = (
                goal_match.group(1) if goal_match
                else task.goal_id
            )

            if goal_id and findings_match:
                findings = findings_match.group(1).strip()
                self._goal_tree.update_progress(goal_id, findings[:200])

                if question_match:
                    question = question_match.group(1).strip()
                    self._goal_tree.update_question(
                        goal_id, question, findings[:300],
                    )

                self._goal_tree.save()
                log.info("Goal tree updated for %s", goal_id)

        except Exception as e:
            log.warning("Failed to update goal tree: %s", e)

    async def _maybe_update_memory(
        self, task: ExploreItem, summary: str,
    ) -> None:
        """Extract memory-worthy content from exploration output.

        Facts (URLs, API endpoints, versions) -> write immediately.
        Patterns (user preferences, recurring themes) -> mark [PENDING].
        """
        if not self._memory:
            return

        memory_match = re.search(
            r'## (?:Memory建议|Memory Update|记忆更新)\s*\n(.*?)(?=\n## |\Z)',
            summary, re.DOTALL,
        )
        if not memory_match:
            return

        content = memory_match.group(1).strip()
        if not content or len(content) < 30:
            return

        is_fact = any(kw in content.lower() for kw in [
            "url", "http", "api", "版本", "发布", "更新日期",
        ])

        slug = re.sub(r'[^\w]', '_', task.direction[:30]).strip('_').lower()

        if is_fact:
            mem_type = "reference"
            description = f"探索发现 -- {task.direction[:60]}"
            filename = f"explore_ref_{slug}.md"
        else:
            mem_type = "project"
            description = f"[PENDING] 探索模式 -- {task.direction[:60]}"
            filename = f"explore_pending_{slug}.md"
            content = (
                f"**状态**: 待确认（单次探索发现，需要更多信号验证）\n\n"
                f"{content}"
            )

        updates = [{
            "type": mem_type,
            "filename": filename,
            "action": "create",
            "description": description,
            "content": content,
        }]

        try:
            # MemoryStore.apply_updates uses a user_id namespace
            self._memory.apply_updates("_system", updates)
            log.info("Memory written (%s): %s", mem_type, filename)
        except Exception as e:
            log.warning("Memory write failed: %s", e)

    def _build_exploration_map(self) -> list[dict]:
        """Build exploration map from recently completed items."""
        done = self._queue.list_items(status="done")
        # Last 20 completed items
        return [
            {
                "title": item.direction,
                "summary": item.result_summary[:200],
            }
            for item in done[-20:]
        ]

    async def _send_task_card(
        self, task: ExploreItem, result: ExploreResult,
    ) -> None:
        """Send a Feishu card notification for a completed exploration task."""
        # Extract conclusion section if present
        conclusion_match = re.search(
            r'## 结论\s*\n(.+?)(?=\n## |\Z)', result.text, re.DOTALL,
        )
        conclusion = (
            conclusion_match.group(1).strip()[:500]
            if conclusion_match
            else result.text[:500]
        )

        status_icon = "✅" if result.success else "❌"
        text = (
            f"{{{{card:header=[探索] {task.direction[:40]},color=turquoise}}}}\n"
            f"{status_icon} **{task.direction}** [{task.priority}]\n\n"
            f"模型: {result.model_used} | 耗时: {int(result.duration_s)}s\n\n"
            f"---\n\n"
            f"{conclusion}"
        )

        try:
            await self._notify(self._notify_open_id, text)
        except Exception as e:
            log.warning("Explorer notification failed: %s", e)

    async def _send_session_summary(
        self, results: list[ExploreResult], elapsed: int,
    ) -> None:
        """Send a summary card for the entire exploration session."""
        done = sum(1 for r in results if r.success)
        lines = [
            f"{{{{card:header=探索会话完成,color=green}}}}\n",
            f"**完成**: {done}/{len(results)} 任务 | **耗时**: {elapsed}s\n",
        ]
        for r in results:
            icon = "✅" if r.success else "❌"
            lines.append(
                f"- {icon} {r.item.direction[:50]} "
                f"({int(r.duration_s)}s, {r.model_used})"
            )

        try:
            await self._notify(
                self._notify_open_id, "\n".join(lines),
            )
        except Exception as e:
            log.warning("Session summary notification failed: %s", e)
