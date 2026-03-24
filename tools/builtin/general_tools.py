# -*- coding: utf-8 -*-
"""General-purpose built-in tools — file, web, system operations."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil

from core.tool_registry import tool

log = logging.getLogger("agentic.tools.general")

# ---------------------------------------------------------------------------
# Persistent working directory for bash
# ---------------------------------------------------------------------------
_cwd: str = os.getcwd()

# Image extensions (placeholder — no multimodal tool-result support yet)
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".svg", ".ico"})


@tool(description="Read a file. Output includes line numbers (cat -n format). Supports offset and max_lines for large files. Detects PDF and image files.", parallel_safe=True)
async def read_file(path: str, offset: int = 0, max_lines: int = 2000) -> str:
    """Read content from a file with line numbers.

    Args:
        path: Absolute or relative file path
        offset: Skip first N lines (1-based). 0 means start from beginning.
        max_lines: Maximum number of lines to read (default 2000)
    """
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return f"Error: file not found: {path}"

    ext = os.path.splitext(path)[1].lower()

    # --- Image detection (placeholder) ---
    if ext in _IMAGE_EXTS:
        size = os.path.getsize(path)
        return (
            f"[Image file: {os.path.basename(path)} ({size} bytes)]\n"
            "This is an image file. The current tool-result pipeline does not "
            "support inline images. Use bash to inspect metadata (e.g. `file` "
            "or `exiftool`)."
        )

    # --- PDF support (best-effort) ---
    if ext == ".pdf":
        try:
            import pypdf  # noqa: F811
            reader = pypdf.PdfReader(path)
            pages_text = []
            for i, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(f"--- Page {i} ---\n{text}")
            if not pages_text:
                return f"[PDF: {os.path.basename(path)}, {len(reader.pages)} pages — no extractable text]"
            content = "\n".join(pages_text)
            lines = content.splitlines()[:max_lines]
            numbered = [f"  {i:>4}\t{line}" for i, line in enumerate(lines, 1)]
            return "\n".join(numbered)
        except ImportError:
            return (
                f"[PDF file: {os.path.basename(path)} — pypdf not installed, "
                "cannot extract text. Install with: pip install pypdf]"
            )
        except Exception as e:
            return f"Error reading PDF {path}: {e}"

    # --- Normal text file ---
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # Apply offset (1-based)
        if offset > 0:
            start_line = offset
            lines = lines[offset - 1:]
        else:
            start_line = 1

        # Truncate
        truncated = len(lines) > max_lines
        lines = lines[:max_lines]

        # Format with line numbers (cat -n style: "  {num}\t{content}")
        numbered = []
        for i, line in enumerate(lines):
            lineno = start_line + i
            numbered.append(f"  {lineno:>4}\t{line.rstrip()}")

        result = "\n".join(numbered)
        if truncated:
            result += f"\n... (truncated at {max_lines} lines)"
        return result
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


@tool(description="Search file contents using regex. Supports output modes (content/files_with_matches/count), context lines, case-insensitive, and multiline. Uses ripgrep if available.", parallel_safe=True)
async def grep(
    pattern: str,
    path: str = ".",
    glob_filter: str = "",
    output_mode: str = "content",
    context: int = 0,
    case_insensitive: bool = False,
    multiline: bool = False,
    max_results: int = 50,
) -> str:
    """Search for a regex pattern in files.

    Args:
        pattern: Regular expression pattern to search for
        path: File or directory to search in (default: current directory)
        glob_filter: Optional glob to filter files (e.g., '*.py')
        output_mode: 'content' (matching lines), 'files_with_matches' (file paths only), 'count' (match counts per file)
        context: Number of context lines before and after each match (like grep -C)
        case_insensitive: Case insensitive search
        multiline: Enable multiline matching (pattern can span lines)
        max_results: Maximum number of results to return (default 50)
    """
    path = os.path.expanduser(path)

    # --- Try ripgrep first ---
    rg_path = shutil.which("rg")
    if rg_path:
        return await _grep_rg(rg_path, pattern, path, glob_filter, output_mode, context, case_insensitive, multiline, max_results)

    # --- Fallback: Python re ---
    return _grep_python(pattern, path, glob_filter, output_mode, context, case_insensitive, multiline, max_results)


async def _grep_rg(
    rg_path: str, pattern: str, path: str, glob_filter: str,
    output_mode: str, context: int, case_insensitive: bool,
    multiline: bool, max_results: int,
) -> str:
    """ripgrep-based search."""
    cmd = [rg_path, pattern, path, "--no-heading", "--with-filename"]
    if glob_filter:
        cmd += ["--glob", glob_filter]
    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")
    if context > 0 and output_mode == "content":
        cmd += ["-C", str(context)]
    if case_insensitive:
        cmd.append("-i")
    if multiline:
        cmd += ["-U", "--multiline-dotall"]
    # max results
    if output_mode in ("content", "files_with_matches"):
        cmd += ["-m", str(max_results)]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace").rstrip()
        if not output:
            if proc.returncode == 1:
                return "No matches found"
            if stderr:
                return f"rg error: {stderr.decode('utf-8', errors='replace').rstrip()}"
            return "No matches found"
        # Truncate
        lines = output.split("\n")
        if len(lines) > max_results * 3:  # generous limit for context lines
            output = "\n".join(lines[:max_results * 3]) + f"\n... (truncated at {max_results} results)"
        return output
    except asyncio.TimeoutError:
        return "Search timed out after 30s"
    except Exception as e:
        return f"rg error: {e}"


def _grep_python(
    pattern: str, path: str, glob_filter: str,
    output_mode: str, context: int, case_insensitive: bool,
    multiline: bool, max_results: int,
) -> str:
    """Pure-Python regex fallback."""
    import re
    import fnmatch

    flags = 0
    if case_insensitive:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.DOTALL

    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"Error: invalid regex: {e}"

    results: list[str] = []
    file_counts: dict[str, int] = {}

    def _search_file(fpath: str) -> None:
        if multiline:
            _search_file_multiline(fpath)
            return
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except (OSError, UnicodeDecodeError):
            return

        for lineno_idx, line in enumerate(all_lines):
            if len(results) >= max_results and output_mode == "content":
                return
            if regex.search(line):
                if output_mode == "files_with_matches":
                    if fpath not in file_counts:
                        results.append(fpath)
                        file_counts[fpath] = 1
                    return
                elif output_mode == "count":
                    file_counts[fpath] = file_counts.get(fpath, 0) + 1
                else:  # content
                    if context > 0:
                        start = max(0, lineno_idx - context)
                        end = min(len(all_lines), lineno_idx + context + 1)
                        for ci in range(start, end):
                            sep = ":" if ci == lineno_idx else "-"
                            results.append(f"{fpath}{sep}{ci + 1}{sep} {all_lines[ci].rstrip()}")
                        results.append("--")
                    else:
                        results.append(f"{fpath}:{lineno_idx + 1}: {line.rstrip()}")

    def _search_file_multiline(fpath: str) -> None:
        try:
            content = open(fpath, "r", encoding="utf-8", errors="replace").read()
        except (OSError, UnicodeDecodeError):
            return
        matches = list(regex.finditer(content))
        if not matches:
            return
        if output_mode == "files_with_matches":
            results.append(fpath)
            return
        if output_mode == "count":
            file_counts[fpath] = file_counts.get(fpath, 0) + len(matches)
            return
        for m in matches[:max_results - len(results)]:
            lineno = content[:m.start()].count("\n") + 1
            matched_text = m.group()[:200]  # cap matched text
            results.append(f"{fpath}:{lineno}: {matched_text}")

    if os.path.isfile(path):
        _search_file(path)
    elif os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            _dirs[:] = [d for d in _dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]
            for fname in files:
                if len(results) >= max_results:
                    break
                if glob_filter and not fnmatch.fnmatch(fname, glob_filter):
                    continue
                _search_file(os.path.join(root, fname))
    else:
        return f"Error: path not found: {path}"

    # Format output
    if output_mode == "count":
        if not file_counts:
            return "No matches found"
        lines = [f"{fpath}:{cnt}" for fpath, cnt in sorted(file_counts.items())]
        return "\n".join(lines[:max_results])

    if not results:
        return "No matches found"
    output = "\n".join(results)
    if len(results) >= max_results:
        output += f"\n... (truncated at {max_results} results)"
    return output


@tool(description="Execute a shell command. Working directory persists between calls. Output truncated at 50KB. Default timeout 120s (max 600s).", parallel_safe=False, timeout=600.0)
async def bash(command: str, timeout: int = 120, run_in_background: bool = False) -> str:
    """Execute a shell command and return output.

    Args:
        command: Shell command to execute
        timeout: Timeout in seconds (default 120, max 600)
        run_in_background: If True, start in background and return immediately
    """
    global _cwd
    timeout = max(1, min(timeout, 600))

    # Ensure _cwd still exists
    if not os.path.isdir(_cwd):
        _cwd = os.path.expanduser("~")

    if run_in_background:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=_cwd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return f"Started in background (PID {proc.pid})"
        except Exception as e:
            return f"Error starting background command: {e}"

    # Wrap command to track cwd changes
    sentinel = "___AF_CWD_SENTINEL___"
    wrapped = f'cd "{_cwd}" && {{ {command} ; }}; __af_rc=$?; echo "{sentinel}$(pwd)"; exit $__af_rc'

    try:
        proc = await asyncio.create_subprocess_shell(
            wrapped,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout_bytes.decode("utf-8", errors="replace")

        # Extract cwd from sentinel
        if sentinel in output:
            parts = output.rsplit(sentinel, 1)
            output = parts[0]
            new_cwd = parts[1].strip()
            if new_cwd and os.path.isdir(new_cwd):
                _cwd = new_cwd

        if stderr_bytes:
            err = stderr_bytes.decode("utf-8", errors="replace")
            output += f"\n[stderr]\n{err}"
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"
        # Truncate very long output
        if len(output) > 50000:
            output = output[:50000] + "\n... (truncated)"
        return output.strip() or "(no output)"
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
