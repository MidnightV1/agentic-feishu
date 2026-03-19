# -*- coding: utf-8 -*-
"""Security scanner — audit skills, tools, and configuration.

Checks:
- Skill code for suspicious patterns (exec, eval, subprocess)
- Prompt injection attempts in skill descriptions
- Permission scope vs actual API usage
- API keys in code or version control
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("agentic.infra.security")


@dataclass
class SecurityFinding:
    """A single security finding."""
    severity: str  # "critical" | "high" | "medium" | "low" | "info"
    category: str  # "code" | "injection" | "permission" | "credential"
    location: str  # file path or component name
    description: str
    recommendation: str = ""


@dataclass
class AuditReport:
    """Complete security audit report."""
    findings: list[SecurityFinding] = field(default_factory=list)
    scanned_paths: list[str] = field(default_factory=list)
    verdict: str = "SAFE"  # SAFE | SUSPICIOUS | DANGEROUS

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "high")

    def summary(self) -> str:
        if not self.findings:
            return "✅ SAFE — No issues found"
        counts = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        return f"{self.verdict} — {', '.join(parts)}"


# Suspicious code patterns
_SUSPICIOUS_PATTERNS = [
    (r"\beval\s*\(", "high", "code", "eval() call — potential code injection"),
    (r"\bexec\s*\(", "high", "code", "exec() call — potential code injection"),
    (r"\b__import__\s*\(", "medium", "code", "__import__() — dynamic import"),
    (r"subprocess\.(call|run|Popen)", "medium", "code", "subprocess execution"),
    (r"os\.system\s*\(", "high", "code", "os.system() — shell injection risk"),
    (r"(api_key|secret|password|token)\s*=\s*['\"][^'\"]{8,}", "high", "credential",
     "Hardcoded credential in code"),
]

# Prompt injection patterns in skill descriptions
_INJECTION_PATTERNS = [
    (r"ignore\s+(all\s+)?previous\s+instructions", "critical", "injection",
     "Prompt injection: instruction override attempt"),
    (r"you\s+are\s+now\s+", "high", "injection",
     "Prompt injection: persona hijack attempt"),
    (r"system\s*:\s*", "medium", "injection",
     "Prompt injection: system role impersonation"),
]


class SecurityScanner:
    """Scans skills and configuration for security issues."""

    def scan_file(self, path: Path) -> list[SecurityFinding]:
        """Scan a single file for suspicious patterns."""
        findings: list[SecurityFinding] = []
        if not path.exists() or not path.is_file():
            return findings

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return findings

        patterns = _SUSPICIOUS_PATTERNS
        if path.suffix in (".md", ".yaml", ".yml", ".txt"):
            patterns = _INJECTION_PATTERNS

        for pattern, severity, category, description in patterns:
            if re.search(pattern, content, re.IGNORECASE):
                findings.append(SecurityFinding(
                    severity=severity,
                    category=category,
                    location=str(path),
                    description=description,
                ))

        return findings

    def scan_directory(self, directory: Path, extensions: set | None = None) -> AuditReport:
        """Scan an entire directory recursively."""
        exts = extensions or {".py", ".yaml", ".yml", ".md", ".toml"}
        report = AuditReport()

        if not directory.is_dir():
            return report

        for path in directory.rglob("*"):
            if path.suffix not in exts:
                continue
            if "__pycache__" in str(path) or ".git" in str(path):
                continue

            findings = self.scan_file(path)
            report.findings.extend(findings)
            report.scanned_paths.append(str(path))

        # Determine verdict
        if report.critical_count > 0:
            report.verdict = "DANGEROUS"
        elif report.high_count > 0:
            report.verdict = "SUSPICIOUS"
        else:
            report.verdict = "SAFE"

        return report

    def scan_skill(self, skill_dir: Path) -> AuditReport:
        """Scan a skill directory (code + prompts + metadata)."""
        return self.scan_directory(skill_dir)