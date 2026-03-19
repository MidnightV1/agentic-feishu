# -*- coding: utf-8 -*-
"""Feishu media handler — image, file, and audio processing.

Downloads media from Feishu API, processes based on type:
- Images: compress via subprocess → store → path for LLM vision
- Files (text/code): read inline (max 10KB)
- Files (PDF): summarize via provider → inject summary
- Audio: transcribe via provider → inject text
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("agentic.feishu.media")

# Text/code extensions that can be read inline
_TEXT_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml",
    ".yml", ".toml", ".xml", ".html", ".css", ".csv", ".sql", ".sh",
    ".bash", ".zsh", ".go", ".rs", ".java", ".kt", ".c", ".cpp", ".h",
    ".rb", ".php", ".swift", ".r", ".lua", ".conf", ".ini", ".env",
    ".log", ".diff", ".patch", ".vue", ".svelte",
}

_MAX_INLINE_CHARS = 10_000
_MAX_IMAGE_DIM = 1024


class MediaHandler:
    """Processes media attachments from Feishu messages."""

    def __init__(
        self,
        api: Any,
        data_dir: str = "data",
        compress_script: str = "",
    ):
        self._api = api
        self._data_dir = Path(data_dir)
        self._files_dir = self._data_dir / "files"
        self._tmp_dir = self._data_dir / "tmp"
        self._files_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir.mkdir(parents=True, exist_ok=True)
        self._compress_script = compress_script or str(
            Path(__file__).parent.parent.parent / "scripts" / "compress_image.py"
        )

    # ── Public API ────────────────────────────────────────────────

    async def process_image(
        self,
        message_id: str,
        image_key: str,
        session_key: str,
    ) -> str:
        """Download, compress, and store an image. Returns file path."""
        raw_path = self._tmp_dir / f"feishu_raw_{image_key[:16]}.dat"
        try:
            # Download
            data = await self._api.download_resource(message_id, image_key, "image")
            if not data:
                return "[图片下载失败]"
            raw_path.write_bytes(data)

            # Compress via subprocess (isolate PIL from main process)
            out_path = self._session_path(session_key, f"{image_key[:16]}.webp")
            ok = await self._compress_image(raw_path, out_path)
            if not ok:
                # Fallback: store raw
                out_path = self._session_path(session_key, f"{image_key[:16]}.dat")
                raw_path.rename(out_path)

            return f"[图片] {out_path}"
        except Exception as e:
            log.warning("Image processing failed: %s", e)
            return f"[图片处理失败: {e}]"
        finally:
            if raw_path.exists():
                raw_path.unlink(missing_ok=True)

    async def process_file(
        self,
        message_id: str,
        file_key: str,
        file_name: str,
        session_key: str,
    ) -> str:
        """Download and process a file attachment. Returns context string."""
        try:
            data = await self._api.download_resource(message_id, file_key, "file")
            if not data:
                return f"[文件下载失败: {file_name}]"

            stored = self._session_path(session_key, file_name)
            stored.write_bytes(data)

            ext = Path(file_name).suffix.lower()

            # Text/code files: inline content
            if ext in _TEXT_EXTS:
                return self._read_text_file(stored, file_name)

            # PDF: store path, LLM can use Read tool
            if ext == ".pdf":
                return (
                    f"[用户发送了 PDF 文件: {file_name}]\n"
                    f"文件路径: {stored}\n"
                    f"请使用 read_file 工具读取此文件内容。"
                )

            # Other: just store
            return (
                f"[用户发送了文件: {file_name}]\n"
                f"文件路径: {stored}\n"
                f"文件类型: {ext or '未知'}"
            )
        except Exception as e:
            log.warning("File processing failed: %s", e)
            return f"[文件处理失败: {file_name} - {e}]"

    async def process_audio(
        self,
        message_id: str,
        file_key: str,
        session_key: str,
    ) -> str:
        """Download audio and return path for transcription."""
        try:
            data = await self._api.download_resource(message_id, file_key, "file")
            if not data:
                return "[语音下载失败]"

            stored = self._session_path(session_key, f"audio_{file_key[:16]}.opus")
            stored.write_bytes(data)
            return (
                f"[语音消息]\n"
                f"音频文件路径: {stored}\n"
                f"请使用工具转写此音频。"
            )
        except Exception as e:
            log.warning("Audio processing failed: %s", e)
            return f"[语音处理失败: {e}]"

    # ── Internal ──────────────────────────────────────────────────

    def _session_path(self, session_key: str, filename: str) -> Path:
        """Get storage path for a session's file."""
        safe_key = session_key.replace(":", "_").replace("/", "_")
        d = self._files_dir / safe_key
        d.mkdir(parents=True, exist_ok=True)
        return d / filename

    def _read_text_file(self, path: Path, file_name: str) -> str:
        """Read a text file inline."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            if len(content) > _MAX_INLINE_CHARS:
                content = content[:_MAX_INLINE_CHARS] + (
                    f"\n\n... [截断，总计 {len(content)} 字符]"
                )
            return f"[用户发送了文件: {file_name}]\n```\n{content}\n```"
        except Exception as e:
            return f"[文件读取失败: {file_name} - {e}]"

    async def _compress_image(self, src: Path, dst: Path) -> bool:
        """Compress image via subprocess (PIL isolation)."""
        script = self._compress_script
        if not Path(script).exists():
            log.warning("Compress script not found: %s, storing raw", script)
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", script,
                str(src), str(dst),
                str(_MAX_IMAGE_DIM),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0:
                log.warning("Image compress failed: %s", stderr.decode())
                return False
            return dst.exists()
        except asyncio.TimeoutError:
            log.warning("Image compress timeout")
            return False
        except Exception as e:
            log.warning("Image compress error: %s", e)
            return False
