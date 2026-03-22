# -*- coding: utf-8 -*-
"""Organization context — soul (static) + cognition (dynamic).

Soul: mission, values, principles — rarely changes (admin-edited).
Cognition: org status, global facts — daily update (cron or admin).

Files:
    data/org/soul.md
    data/org/cognition.md
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("agentic.infra.org_context")


class OrgContext:
    """Loads and serves organization-level context."""

    def __init__(self, base_dir: str = "data/org"):
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _read(self, filename: str) -> str:
        path = self._dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return ""

    def _write(self, filename: str, content: str) -> None:
        path = self._dir / filename
        path.write_text(content, encoding="utf-8")

    # ── Soul ──

    @property
    def soul(self) -> str:
        return self._read("soul.md")

    # ── Cognition ──

    @property
    def cognition(self) -> str:
        return self._read("cognition.md")

    def update_cognition(self, content: str) -> None:
        """Update org cognition (called by daily cron job)."""
        self._write("cognition.md", content)
        log.info("Org cognition updated (%d chars)", len(content))
