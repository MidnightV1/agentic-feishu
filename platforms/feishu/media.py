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
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Coroutine

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
_MIN_PDF_TEXT_LEN = 100  # Minimum chars for pypdf to be considered successful


class MediaHandler:
    """Processes media attachments from Feishu messages."""

    def __init__(
        self,
        api: Any,
        data_dir: str = "data",
        compress_script: str = "",
        pdf_analyzer: Callable[[bytes, str], Coroutine[Any, Any, str]] | None = None,
    ):
        self._api = api
        self._pdf_analyzer = pdf_analyzer
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

            # PDF: 3-tier fallback chain
            if ext == ".pdf":
                return await self._process_pdf(stored, file_name, data)

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

    @staticmethod
    async def extract_pdf_text(file_path: str, max_pages: int = 20) -> str:
        """Extract text from PDF. Returns extracted text or error message."""
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            pages = reader.pages[:max_pages]
            text = "\n\n".join(page.extract_text() or "" for page in pages)
            if text.strip():
                total = len(reader.pages)
                suffix = f"\n\n[Extracted {min(max_pages, total)}/{total} pages]" if total > max_pages else ""
                return text.strip() + suffix
            return f"[PDF has {len(reader.pages)} pages but text extraction failed - may be image-based]"
        except ImportError:
            return f"[PDF at {file_path} - pypdf not available for text extraction]"
        except Exception as e:
            return f"[PDF extraction error: {e}]"

    async def summarize_pdf(self, file_path: str) -> str:
        """Extract text from a PDF for LLM processing.

        Returns extracted text content. The caller (LLM session) can then
        process/summarize the text as needed.
        """
        text = await self.extract_pdf_text(file_path)
        if text.startswith("["):
            # Extraction failed — return error message as-is
            return text
        # Truncate if very long (LLM context limit consideration)
        max_chars = 50_000
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n... [truncated at {max_chars} chars]"
        return text

    async def _process_pdf(self, stored: "Path", file_name: str, raw_data: bytes) -> str:
        """Process PDF with 3-tier fallback chain.

        Tier 1: pypdf text extraction (fast, free)
        Tier 2: Gemini API vision analysis (for image-based PDFs)
        Tier 3: Return path hint for LLM read_file tool
        """
        # Tier 1: pypdf
        text = await self.extract_pdf_text(str(stored))
        if not text.startswith("[") and len(text.strip()) >= _MIN_PDF_TEXT_LEN:
            # pypdf succeeded with meaningful content
            max_chars = 50_000
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n\n... [truncated at {max_chars} chars]"
            return (
                f"[用户发送了 PDF 文件: {file_name}]\n"
                f"以下是提取的文本内容:\n\n{text}"
            )

        # Tier 2: Gemini API analysis (for scanned/image-based PDFs)
        if self._pdf_analyzer:
            try:
                log.info("PDF pypdf insufficient for %s, trying Gemini API", file_name)
                analysis = await self._pdf_analyzer(raw_data, file_name)
                if analysis and not analysis.startswith("["):
                    return (
                        f"[用户发送了 PDF 文件: {file_name}]\n"
                        f"以下是 AI 分析的内容:\n\n{analysis}"
                    )
            except Exception as e:
                log.warning("Gemini PDF analysis failed for %s: %s", file_name, e)

        # Tier 3: Fallback to path hint
        return (
            f"[用户发送了 PDF 文件: {file_name}]\n"
            f"文件路径: {stored}\n"
            f"请使用 read_file 工具读取此文件内容。"
        )

    async def expand_merged_forward(self, message_id: str) -> list[str]:
        """Expand a merged-forward message to get individual sub-messages as text.

        Uses the FeishuAPI client to fetch sub-messages and parse their content.

        Args:
            message_id: The merged forward message ID.

        Returns:
            List of text strings extracted from sub-messages.
        """
        sub_messages = await self._api.get_merged_forward_messages(message_id)
        texts: list[str] = []
        for msg in sub_messages:
            if "error" in msg:
                texts.append(f"[Error: {msg['error']}]")
                continue
            msg_type = msg.get("msg_type", "")
            content = msg.get("content", "")
            parsed = self.parse_content(msg_type, content)
            if parsed:
                texts.append(parsed)
        return texts

    # ── Content Parsing ────────────────────────────────────────────

    @staticmethod
    def parse_content(msg_type: str, content_str: str) -> str:
        """Parse Feishu message content to plain text.

        Handles: text, post, interactive (card), markdown.
        For unknown types, attempts best-effort extraction.
        """
        try:
            content = (
                json.loads(content_str) if isinstance(content_str, str) else content_str
            )
        except json.JSONDecodeError:
            return content_str if isinstance(content_str, str) else ""

        if not isinstance(content, dict):
            return str(content) if content else ""

        if msg_type == "text":
            text = content.get("text", "")
            # Strip @_user_N_ mention placeholders
            return re.sub(r"@_user_\d+\s*", "", text).strip()

        if msg_type == "post":
            return MediaHandler._parse_post(content)

        if msg_type == "interactive":
            return MediaHandler._parse_card(content)

        if msg_type == "markdown":
            return content.get("text", "")

        # Fallback: try common fields
        for key in ("text", "content"):
            val = content.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""

    @staticmethod
    def _parse_post(content: dict) -> str:
        """Parse rich-text post content.

        Handles both flat {title, content: [[...]]} and
        multi-language {zh_cn: {title, content}} structures.
        """
        # Flat structure (most common)
        if "content" in content and isinstance(content["content"], list):
            return MediaHandler._extract_post_body(content)
        # Multi-language — use first available
        for lang_content in content.values():
            if isinstance(lang_content, dict) and "content" in lang_content:
                return MediaHandler._extract_post_body(lang_content)
        return ""

    @staticmethod
    def _extract_post_body(post: dict) -> str:
        """Extract text from a single post body {title, content: [[elements]]}."""
        lines: list[str] = []
        title = post.get("title")
        if title:
            lines.append(title)
        for para in post.get("content", []):
            if not isinstance(para, list):
                continue
            parts: list[str] = []
            for elem in para:
                tag = elem.get("tag", "")
                if tag == "text":
                    parts.append(elem.get("text", ""))
                elif tag == "a":
                    text = elem.get("text", "")
                    href = elem.get("href", "")
                    parts.append(f"[{text}]({href})" if href else text)
                elif tag == "at":
                    parts.append(elem.get("name", ""))
                # img, media, hr — skip, no text content
            if parts:
                lines.append("".join(parts))
        return "\n".join(lines)

    @staticmethod
    def _parse_card(content: dict) -> str:
        """Extract text from an interactive card.

        Supports JSON 2.0 (body.elements) and legacy 1.0 (elements) formats.
        """
        parts: list[str] = []

        # Header title
        header = content.get("header", {})
        title_obj = header.get("title", {})
        title_text = title_obj.get("content", "")
        if title_text:
            parts.append(title_text)

        # JSON 2.0: body.elements[].tag=="markdown"
        for el in content.get("body", {}).get("elements", []):
            if isinstance(el, dict) and el.get("tag") == "markdown":
                parts.append(el.get("content", ""))

        # Fallback: JSON 1.0 legacy / degraded format
        for el in content.get("elements", []):
            if isinstance(el, dict):
                if el.get("tag") == "markdown":
                    parts.append(el.get("content", ""))
                elif el.get("tag") == "div":
                    text_obj = el.get("text", {})
                    if isinstance(text_obj, dict):
                        parts.append(text_obj.get("content", ""))

        return "\n".join(p for p in parts if p)

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
