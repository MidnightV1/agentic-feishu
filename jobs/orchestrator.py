# -*- coding: utf-8 -*-
"""Orchestrator — multi-agent parallel task execution from <task_plan> tags.

Pipeline:
1. Receive task_plan JSON from LLM output
2. Display plan to user for confirmation
3. On approval, spawn parallel workers (one per subtask)
4. Collect results and present summary
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

log = logging.getLogger("agentic.jobs.orchestrator")


@dataclass
class SubtaskResult:
    """Result of a single subtask execution."""
    title: str
    status: str = "pending"  # pending | running | success | failed
    output: str = ""
    duration_s: float = 0
    error: str = ""


@dataclass
class TaskPlan:
    """Parsed task plan from <task_plan> JSON."""
    description: str
    original_task: str
    subtasks: list[dict] = field(default_factory=list)  # [{title, prompt}]
    status: str = "pending"  # pending | approved | running | done
    results: list[SubtaskResult] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


class Orchestrator:
    """Manages parallel subtask execution.

    Usage:
        orch = Orchestrator(
            execute_fn=agent_loop.run,
            notify_fn=dispatcher.send_card,
        )
        plan = orch.parse_plan(json_str)
        await orch.present_plan(plan, chat_id)
        # User approves...
        await orch.execute_plan(plan)
    """

    def __init__(
        self,
        execute_fn: Callable[..., Coroutine] | None = None,
        notify_fn: Callable[..., Coroutine] | None = None,
    ):
        self._execute = execute_fn
        self._notify = notify_fn
        self._plans: list[TaskPlan] = []

    def parse_plan(self, json_str: str) -> TaskPlan | None:
        """Parse a task_plan JSON string into a TaskPlan."""
        try:
            data = json.loads(json_str)
            plan = TaskPlan(
                description=data.get("description", ""),
                original_task=data.get("original_task", ""),
                subtasks=data.get("subtasks", []),
            )
            plan.results = [
                SubtaskResult(title=st.get("title", f"subtask-{i}"))
                for i, st in enumerate(plan.subtasks)
            ]
            self._plans.append(plan)
            return plan
        except (json.JSONDecodeError, KeyError) as e:
            log.error("Failed to parse task plan: %s", e)
            return None

    async def present_plan(self, plan: TaskPlan, chat_id: str) -> None:
        """Send the plan to user for approval via dispatcher."""
        if not self._notify:
            return

        lines = [
            f"{{{{card:header=任务编排方案,color=wathet}}}}",
            f"**目标**: {plan.description}",
            "",
            f"**子任务** ({len(plan.subtasks)} 项并行):",
        ]
        for i, st in enumerate(plan.subtasks, 1):
            lines.append(f"{i}. {st.get('title', 'untitled')}")

        lines.append("")
        lines.append("回复「执行」开始并行处理，回复「取消」放弃。")

        await self._notify(chat_id, "\n".join(lines))

    async def execute_plan(self, plan: TaskPlan) -> list[SubtaskResult]:
        """Execute all subtasks in parallel."""
        if not self._execute:
            log.warning("No execute function configured for orchestrator")
            return plan.results

        plan.status = "running"

        async def _run_subtask(idx: int, subtask: dict) -> None:
            result = plan.results[idx]
            result.status = "running"
            start = time.monotonic()
            try:
                output = await self._execute(
                    prompt=subtask.get("prompt", ""),
                )
                result.status = "success"
                result.output = str(output)[:2000] if output else ""
            except Exception as e:
                result.status = "failed"
                result.error = str(e)
            result.duration_s = round(time.monotonic() - start, 1)

        # Run all subtasks in parallel
        tasks = [
            _run_subtask(i, st)
            for i, st in enumerate(plan.subtasks)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        plan.status = "done"
        succeeded = sum(1 for r in plan.results if r.status == "success")
        log.info("Plan completed: %d/%d succeeded", succeeded, len(plan.results))
        return plan.results

    def format_results(self, plan: TaskPlan) -> str:
        """Format plan results as markdown for display."""
        lines = [f"{{{{card:header=任务完成,color=green}}}}"]
        lines.append(f"**{plan.description}**\n")

        for r in plan.results:
            icon = "✅" if r.status == "success" else "❌"
            line = f"{icon} **{r.title}** ({r.duration_s}s)"
            if r.error:
                line += f"\n  错误: {r.error}"
            lines.append(line)

        succeeded = sum(1 for r in plan.results if r.status == "success")
        lines.append(f"\n结果: {succeeded}/{len(plan.results)} 成功")
        return "\n".join(lines)
