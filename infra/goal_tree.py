# -*- coding: utf-8 -*-
"""OKR goal tree — YAML persistence + LLM prompt injection.

Loads/saves the goal tree from YAML. Provides query interface for
the explorer, scheduler, and LLM system prompt injection.
"""

import logging
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)


class GoalTree:
    """OKR goal tree with YAML persistence."""

    def __init__(self, path: str = "data/goal_tree.yaml"):
        self._path = path
        self._data: dict = {}

    # ── Persistence ──

    def load(self) -> None:
        """Load goal tree from YAML file."""
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = yaml.safe_load(f) or {}
            log.debug("Goal tree loaded: %d goals",
                      len(self._data.get("goals", [])))
        except FileNotFoundError:
            log.warning("Goal tree not found: %s — using empty tree", self._path)
            self._data = {"version": 1, "mission": "", "goals": []}
        except Exception as e:
            log.error("Goal tree load error: %s", e)
            self._data = {"version": 1, "mission": "", "goals": []}

    def save(self) -> None:
        """Save goal tree to YAML file."""
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(self._data, f, allow_unicode=True,
                      default_flow_style=False, sort_keys=False, width=120)
        log.info("Goal tree saved to %s", self._path)

    # ── Queries ──

    def get_active_goals(self) -> list[dict]:
        """Return goals with status 'exploring' or 'blocked'."""
        return [
            g for g in self._data.get("goals", [])
            if g.get("status") in ("exploring", "blocked")
        ]

    def get_open_questions(self, priority: Optional[str] = None) -> list[dict]:
        """Return all open sub_questions across active goals, flattened.

        Each item: {"goal_id", "goal_title", "priority", "question", "findings"}
        """
        questions = []
        for goal in self.get_active_goals():
            if priority and goal.get("priority") != priority:
                continue
            for sq in goal.get("sub_questions", []):
                if sq.get("status") == "open":
                    questions.append({
                        "goal_id": goal["id"],
                        "goal_title": goal["title"],
                        "priority": goal.get("priority", "P2"),
                        "question": sq["q"],
                        "findings": sq.get("findings", ""),
                    })
        return questions

    # ── LLM Prompt Injection ──

    def format_for_prompt(self, max_goals: int = 10) -> str:
        """Format goal tree as Chinese markdown for LLM system prompt injection.

        Output: emoji status icons + OKR structure (mission → goals → questions).
        """
        parts = []
        mission = self._data.get("mission", "")
        if mission:
            parts.append(f"## 系统使命\n{mission}\n")

        goals = self.get_active_goals()[:max_goals]
        if not goals:
            return "\n".join(parts) + "\n(无活跃目标)"

        parts.append("## 活跃目标 (OKR)")
        for g in goals:
            status_icon = {"exploring": "🔍", "blocked": "🚧"}.get(
                g["status"], "")
            parts.append(
                f"\n### [{g.get('priority', 'P2')}] {g['title']} {status_icon}")
            parts.append(f"*Why*: {g.get('why', '')}")

            open_qs = [sq for sq in g.get("sub_questions", [])
                       if sq.get("status") == "open"]
            if open_qs:
                parts.append("**待回答的问题**:")
                for sq in open_qs:
                    findings = sq.get("findings", "")
                    suffix = f" — 已知: {findings}" if findings else ""
                    parts.append(f"- {sq['q']}{suffix}")

            progress = g.get("progress", "")
            if progress:
                parts.append(f"*进展*: {progress}")

            krs = g.get("key_results", [])
            if krs:
                parts.append("*关键结果*: " + " | ".join(krs))

        return "\n".join(parts)

    # ── Mutations ──

    def update_progress(self, goal_id: str, progress: str) -> bool:
        """Update a goal's progress notes."""
        for goal in self._data.get("goals", []):
            if goal.get("id") == goal_id:
                goal["progress"] = progress
                return True
        log.warning("Goal not found: %s", goal_id)
        return False

    def update_question(self, goal_id: str, question: str,
                        findings: str, status: Optional[str] = None) -> bool:
        """Update a sub_question's findings. Match by question text."""
        for goal in self._data.get("goals", []):
            if goal.get("id") != goal_id:
                continue
            for sq in goal.get("sub_questions", []):
                if sq["q"] == question:
                    sq["findings"] = findings
                    if status:
                        sq["status"] = status
                    return True
        log.warning("Question not found: goal=%s q=%s", goal_id, question[:50])
        return False
