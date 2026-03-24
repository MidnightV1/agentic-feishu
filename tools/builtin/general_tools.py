# -*- coding: utf-8 -*-
"""General-purpose built-in tools — file, web, system operations."""

from __future__ import annotations

import asyncio
import logging
import os

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.general")


@tool(description="Read a file from the filesystem", parallel_safe=True)
async def read_file(path: str, max_lines: int = 500) -> str:
    """Read content from a file.

    Args:
        path: Absolute or relative file path
        max_lines: Maximum number of lines to read (default 500)
    """
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return f"Error: file not found: {path}"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    lines.append(f"\n... (truncated at {max_lines} lines)")
                    break
                lines.append(line)
        return "".join(lines)
    except Exception as e:
        return f"Error reading {path}: {e}"


@tool(description="Write content to a file, creating directories as needed. Overwrites existing content — verify intent before overwriting.", parallel_safe=False)
async def write_file(path: str, content: str) -> str:
    """Write content to a file, creating directories as needed.

    Args:
        path: Target file path
        content: Content to write
    """
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error writing {path}: {e}"


@tool(description="List files in a directory or find files matching a glob pattern (supports ** for recursive matching). Returns full paths.", parallel_safe=True)
async def list_directory(path: str = ".", pattern: str = "") -> str:
    """List files in a directory or find files matching a glob pattern.

    Args:
        path: Directory path (default: current directory)
        pattern: Optional glob pattern to filter (e.g., '*.py', '**/*.ts' for recursive)
    """
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        return f"Error: not a directory: {path}"

    try:
        if pattern:
            import glob as _glob
            matches = _glob.glob(os.path.join(path, pattern), recursive=True)
            entries = sorted(matches, key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
        else:
            entries = [os.path.join(path, e) for e in sorted(os.listdir(path))]

        if not entries:
            return "(empty directory)"
        return "\n".join(entries[:200])
    except Exception as e:
        return f"Error listing {path}: {e}"


@tool(description="Edit a file by exact string replacement. old_string must match exactly one location in the file. Use replace_all=true to replace all occurrences.", parallel_safe=False)
async def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Edit a file by replacing exact string matches.

    Args:
        path: File path to edit
        old_string: Exact text to find and replace
        new_string: Replacement text (must differ from old_string)
        replace_all: If true, replace all occurrences; otherwise require exactly one match
    """
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return f"Error: file not found: {path}"
    if old_string == new_string:
        return "Error: old_string and new_string are identical"
    try:
        content = open(path, "r", encoding="utf-8").read()
        count = content.count(old_string)
        if count == 0:
            return "Error: old_string not found in file"
        if count > 1 and not replace_all:
            return f"Error: old_string found {count} times — set replace_all=true or provide more context"
        new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        replaced = count if replace_all else 1
        return f"Replaced {replaced} occurrence(s) in {os.path.basename(path)}"
    except Exception as e:
        return f"Error editing {path}: {e}"


@tool(description="Search file contents using regex pattern (like ripgrep). Returns matching lines with file paths and line numbers.", parallel_safe=True)
async def grep(pattern: str, path: str = ".", glob_filter: str = "", max_results: int = 50) -> str:
    """Search for a regex pattern in files.

    Args:
        pattern: Regular expression pattern to search for
        path: File or directory to search in (default: current directory)
        glob_filter: Optional glob to filter files (e.g., '*.py')
        max_results: Maximum number of matching lines to return (default 50)
    """
    import re

    path = os.path.expanduser(path)
    results: list[str] = []

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex: {e}"

    def _search_file(fpath: str) -> None:
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    if len(results) >= max_results:
                        return
                    if regex.search(line):
                        results.append(f"{fpath}:{lineno}: {line.rstrip()}")
        except (OSError, UnicodeDecodeError):
            pass

    if os.path.isfile(path):
        _search_file(path)
    elif os.path.isdir(path):
        import fnmatch
        for root, _dirs, files in os.walk(path):
            # Skip hidden dirs and common noise
            _dirs[:] = [d for d in _dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]
            for fname in files:
                if len(results) >= max_results:
                    break
                if glob_filter and not fnmatch.fnmatch(fname, glob_filter):
                    continue
                _search_file(os.path.join(root, fname))
    else:
        return f"Error: path not found: {path}"

    if not results:
        return "No matches found"
    output = "\n".join(results)
    if len(results) >= max_results:
        output += f"\n... (truncated at {max_results} results)"
    return output


@tool(description="Execute a shell command. Output truncated at 50KB. Use for system operations only — prefer dedicated tools when available.", parallel_safe=False, timeout=60.0)
async def bash(command: str, timeout: int = 30) -> str:
    """Execute a shell command and return output.

    Args:
        command: Shell command to execute
        timeout: Timeout in seconds (default 30)
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")
        if stderr:
            err = stderr.decode("utf-8", errors="replace")
            output += f"\n[stderr]\n{err}"
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"
        # Truncate very long output
        if len(output) > 50000:
            output = output[:50000] + "\n... (truncated)"
        return output or "(no output)"
    except asyncio.TimeoutError:
        return f"Command timed out after {timeout}s"
    except Exception as e:
        return f"Error executing command: {e}"


@tool(description="Search the web using Brave Search", parallel_safe=True)
async def web_search(query: str, count: int = 5) -> str:
    """Search the web using Brave Search API.

    Args:
        query: Search query
        count: Number of results (default 5)
    """
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        return "Error: BRAVE_API_KEY not set"

    import urllib.request
    import urllib.parse
    import json

    url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={count}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "X-Subscription-Token": api_key,
    })

    try:
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=10))
        data = json.loads(resp.read())
        results = data.get("web", {}).get("results", [])
        lines = []
        for r in results[:count]:
            lines.append(f"**{r.get('title', '')}**")
            lines.append(f"  {r.get('url', '')}")
            desc = r.get("description", "")
            if desc:
                lines.append(f"  {desc}")
            lines.append("")
        return "\n".join(lines) or "No results found"
    except Exception as e:
        return f"Search error: {e}"
