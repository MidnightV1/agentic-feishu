# -*- coding: utf-8 -*-
"""Hub operations skill — scheduler CRUD, service status.

CLI script for skill invocation. No @tool registration.

CLI usage:
    python3 skills/hub-ops/tools.py <action> --params '<json>'
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime
from typing import Any

log = logging.getLogger("agentic.skills.hub_ops")

# Module-level scheduler ref — set by configure()
_scheduler: Any = None


def configure(scheduler: Any) -> None:
    """Inject the Scheduler instance."""
    global _scheduler
    _scheduler = scheduler


def _require_scheduler() -> Any:
    if _scheduler is None:
        raise RuntimeError(
            "Scheduler not configured — call hub_ops.tools.configure(scheduler) first"
        )
    return _scheduler


async def hub_ops(action: str, params: dict = {}) -> dict | str:
    """Dispatch hub operations by action name.

    Args:
        action: One of: job_list, job_enable, job_disable, scheduler_reload, service_status
        params: Action-specific parameters
    """
    scheduler = _require_scheduler()

    if action == "job_list":
        jobs = scheduler.list_jobs(include_disabled=True)
        result = []
        for j in jobs:
            sched = j.cron or j.at_time or f"{j.interval_seconds}s"
            next_run = ""
            if j.next_run_at:
                next_run = datetime.fromtimestamp(j.next_run_at).strftime(
                    "%Y-%m-%d %H:%M"
                )
            result.append({
                "name": j.name,
                "enabled": j.enabled,
                "schedule": sched,
                "next_run": next_run,
                "consecutive_errors": j.consecutive_errors,
                "last_error": j.last_error or "",
            })
        return {"jobs": result, "count": len(result)}

    elif action == "job_enable":
        name = params.get("name", "")
        if not name:
            return {"error": "Missing param: name"}
        ok = scheduler.enable_job(name)
        return {"success": ok, "message": f"Job '{name}' enabled" if ok else f"Job '{name}' not found"}

    elif action == "job_disable":
        name = params.get("name", "")
        if not name:
            return {"error": "Missing param: name"}
        ok = scheduler.disable_job(name)
        return {"success": ok, "message": f"Job '{name}' disabled" if ok else f"Job '{name}' not found"}

    elif action == "scheduler_reload":
        job_count = len(scheduler.list_jobs(include_disabled=True))
        return {"message": "Scheduler state refreshed", "job_count": job_count}

    elif action == "service_status":
        jobs = scheduler.list_jobs(include_disabled=True)
        enabled = [j for j in jobs if j.enabled]
        errored = [j for j in enabled if j.consecutive_errors > 0]
        return {
            "scheduler_running": scheduler._running,
            "total_jobs": len(jobs),
            "enabled_jobs": len(enabled),
            "jobs_with_errors": len(errored),
            "error_details": [
                {"name": j.name, "errors": j.consecutive_errors, "last": j.last_error}
                for j in errored
            ],
        }

    else:
        return {
            "error": f"Unknown action '{action}'",
            "valid_actions": [
                "job_list", "job_enable", "job_disable",
                "scheduler_reload", "service_status",
            ],
        }


# -- CLI entry point ---------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Hub operations")
    parser.add_argument("action", choices=["job_list", "job_enable", "job_disable", "scheduler_reload", "service_status"])
    parser.add_argument("--params", default="{}", help="JSON params")
    args = parser.parse_args()

    # Note: CLI mode requires scheduler to be injected externally.
    # This entry point is for in-process invocation via bash tool.
    print(json.dumps({"error": "hub-ops CLI requires in-process scheduler; use via agent bash tool"}, ensure_ascii=False))
