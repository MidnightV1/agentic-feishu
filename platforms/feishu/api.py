# -*- coding: utf-8 -*-
"""Feishu API client — thin async wrapper over lark-oapi.

Provides document, task, calendar, and message operations.
All methods are async and return dicts/lists.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("agentic.feishu.api")


def _to_timestamp(s: str) -> str:
    """Convert time string to Unix timestamp string.

    Accepts: Unix timestamp, ISO 8601, or common datetime formats.
    Returns seconds since epoch as string.
    """
    s = s.strip()
    # Already a Unix timestamp (seconds)
    if s.isdigit() and len(s) >= 10:
        return s
    # Try ISO / common formats
    from datetime import timedelta
    tz_cn = timezone(offset=timedelta(hours=8))
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz_cn)
            ts = int(dt.timestamp())
            # Sanity: reject timestamps before 2020
            if ts < 1577836800:  # 2020-01-01 UTC
                log.warning("_to_timestamp: parsed %r → %d (before 2020), suspicious", s, ts)
            return str(ts)
        except ValueError:
            continue
    log.warning("_to_timestamp: could not parse %r, returning as-is", s)
    return s


def _to_rfc3339(s: str) -> str:
    """Convert time string to RFC3339 format (required by freebusy API).

    Returns: yyyy-mm-ddThh:mm:ss+08:00
    """
    s = s.strip()
    from datetime import timedelta
    tz_cn = timezone(offset=timedelta(hours=8))

    # Unix timestamp → convert to datetime first
    if s.isdigit() and len(s) >= 10:
        dt = datetime.fromtimestamp(int(s), tz=tz_cn)
        return dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz_cn)
            return dt.strftime("%Y-%m-%dT%H:%M:%S") + dt.strftime("%z")[:3] + ":" + dt.strftime("%z")[3:]
        except ValueError:
            continue
    # Already RFC3339 or can't parse — return as-is
    return s


def _build_descendant_payload(items: list[dict]) -> tuple[list[str], list[dict]]:
    """Build Feishu descendant API payload from flat list items with depth.

    Uses a stack to reconstruct parent-child relationships from flat depth
    values, supporting arbitrary nesting depth.

    Returns (children_id, descendants) where:
    - children_id: top-level temp block IDs
    - descendants: all block definitions with parent-child relationships
    """
    descendants = []
    top_level_ids = []
    # Stack: [(depth, temp_id, desc_dict)]
    stack: list[tuple[int, str, dict]] = []

    for item in items:
        depth = item["depth"]
        bt = 12 if item["type"] == "bullet" else 13
        key = "bullet" if item["type"] == "bullet" else "ordered"
        tid = f"tmp_{uuid.uuid4().hex[:8]}"

        desc = {
            "block_id": tid,
            "block_type": bt,
            key: {"elements": item["elements"]},
        }

        # Pop stack to find parent (first item with depth < current)
        while stack and stack[-1][0] >= depth:
            stack.pop()

        if stack:
            stack[-1][2].setdefault("children", []).append(tid)
        else:
            top_level_ids.append(tid)

        descendants.append(desc)
        stack.append((depth, tid, desc))

    return top_level_ids, descendants


class FeishuAPI:
    """Async Feishu API client using lark-oapi."""

    def __init__(self, app_id: str, app_secret: str, domain: str = "https://open.feishu.cn"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.domain = domain
        self._client = None

    async def start(self) -> None:
        import lark_oapi as lark

        lark_domain = lark.FEISHU_DOMAIN
        if "larksuite" in self.domain:
            lark_domain = lark.LARK_DOMAIN

        self._client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .domain(lark_domain)
            .build()
        )
        self._token_cache: dict[str, Any] = {"token": "", "expires": 0}
        log.info("Feishu API client started")

    async def stop(self) -> None:
        self._client = None
        if hasattr(self, "_http") and self._http:
            await self._http.aclose()
            self._http = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("FeishuAPI not started")
        return self._client

    # ── Documents ─────────────────────────────────────────────────

    async def create_document(self, title: str, folder_token: str = "") -> dict:
        client = self._ensure_client()
        from lark_oapi.api.docx.v1 import CreateDocumentRequest, CreateDocumentRequestBody

        body = CreateDocumentRequestBody.builder().title(title)
        if folder_token:
            body = body.folder_token(folder_token)

        req = CreateDocumentRequest.builder().request_body(body.build()).build()
        resp = await client.docx.v1.document.acreate(req)

        if not resp.success():
            return {"error": f"{resp.code}: {resp.msg}"}
        return {
            "document_id": resp.data.document.document_id,
            "title": resp.data.document.title,
            "url": f"https://feishu.cn/docx/{resp.data.document.document_id}",
        }

    async def get_document_content(self, document_id: str) -> str:
        client = self._ensure_client()
        from lark_oapi.api.docx.v1 import RawContentDocumentRequest

        req = (
            RawContentDocumentRequest.builder()
            .document_id(document_id)
            .build()
        )
        resp = await client.docx.v1.document.araw_content(req)

        if not resp.success():
            return f"Error: {resp.code}: {resp.msg}"
        return resp.data.content

    async def _get_tenant_token(self) -> str:
        """Get cached tenant_access_token for raw HTTP calls."""
        import time as _time
        if self._token_cache["expires"] > _time.time():
            return self._token_cache["token"]

        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{self.domain}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            data = resp.json()
            token = data.get("tenant_access_token", "")
            self._token_cache = {
                "token": token,
                "expires": _time.time() + data.get("expire", 7200) - 60,
            }
            return token

    _TRANSIENT_CODES = {99991663, 99991664, 99991661, 99991668}

    async def _raw_request(self, method: str, path: str,
                           body: dict | None = None,
                           params: dict | None = None) -> dict:
        """Make raw HTTP request to Feishu API with tenant_access_token.

        Retries up to 2 times on network errors or token-expiry codes.
        """
        url = f"{self.domain}{path}"
        if not hasattr(self, "_http") or self._http is None:
            self._http = httpx.AsyncClient(timeout=30)

        last_exc: Exception | None = None
        for attempt in range(3):
            token = await self._get_tenant_token()
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            try:
                resp = await self._http.request(method, url, json=body, params=params, headers=headers)
                try:
                    data = resp.json()
                except ValueError:
                    # Some APIs return non-standard JSON (extra data, etc.)
                    import json as _json
                    text = resp.text.strip()
                    try:
                        decoder = _json.JSONDecoder()
                        parsed, _ = decoder.raw_decode(text)
                        data = parsed if isinstance(parsed, dict) else {"code": resp.status_code, "data": parsed, "msg": "non-dict response"}
                    except _json.JSONDecodeError:
                        data = {"code": resp.status_code or -1, "msg": f"Unparseable response: {text[:200]}"}
                # HTTP error without API-level error code → synthesize one
                if resp.status_code >= 400 and data.get("code") == 0:
                    data["code"] = resp.status_code
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < 2:
                    log.warning("_raw_request network error (attempt %d): %s", attempt + 1, exc)
                    await asyncio.sleep(1 << attempt)  # 1s, 2s
                    continue
                raise

            code = data.get("code")
            if code in self._TRANSIENT_CODES:
                log.warning("_raw_request token-expiry code %s (attempt %d), refreshing", code, attempt + 1)
                self._token_cache = {"token": "", "expires": 0}
                if attempt < 2:
                    await asyncio.sleep(1 << attempt)
                    continue
            return data

        # Should not reach here, but just in case
        if last_exc:
            raise last_exc
        return data  # type: ignore[possibly-undefined]

    async def append_document(self, document_id: str, content: str) -> dict:
        """Append markdown content to a document, with native table support."""
        from platforms.feishu.blocks import text_to_blocks, split_table_rows, TABLE_MAX_COLS

        all_blocks = text_to_blocks(content)
        if not all_blocks:
            return {"ok": True, "blocks_added": 0}

        total = 0
        regular_batch: list[dict] = []
        BATCH_SIZE = 50

        async def _flush_regular():
            nonlocal total
            if not regular_batch:
                return
            for i in range(0, len(regular_batch), BATCH_SIZE):
                chunk = regular_batch[i:i + BATCH_SIZE]
                resp = await self._raw_request(
                    "POST",
                    f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children",
                    body={"children": chunk, "index": -1},
                    params={"document_revision_id": "-1"},
                )
                if resp.get("code") == 0:
                    total += len(chunk)
                else:
                    log.error("Flush %d blocks failed: %s | doc=%s",
                              len(chunk), resp.get("msg"), document_id)
            regular_batch.clear()

        for block in all_blocks:
            if "_table" in block:
                await _flush_regular()
                rows = block["_table"]

                # Truncate columns exceeding API limit
                col_count = max(len(r) for r in rows) if rows else 0
                if col_count > TABLE_MAX_COLS:
                    rows = [r[:TABLE_MAX_COLS] for r in rows]

                chunks = split_table_rows(rows)
                for chunk in chunks:
                    table_bid = await self._create_table_in_doc(document_id, chunk)
                    if table_bid:
                        total += 1
                    else:
                        # Degrade: table failed → write as plain-text pipe rows
                        log.warning("Table degraded to text: %d rows | doc=%s",
                                    len(chunk), document_id)
                        for row in chunk:
                            line = "| " + " | ".join(row) + " |"
                            regular_batch.append({
                                "block_type": 2,
                                "text": {"elements": [{"text_run": {"content": line}}]},
                            })
            elif "_nested_list" in block:
                await _flush_regular()
                count = await self._create_nested_list(document_id, block["_nested_list"])
                if count:
                    total += count
                else:
                    # Degrade: descendant API failed → flat blocks
                    log.warning("Nested list degraded to flat | doc=%s", document_id)
                    for item in block["_nested_list"]:
                        bt = 12 if item["type"] == "bullet" else 13
                        key = "bullet" if item["type"] == "bullet" else "ordered"
                        regular_batch.append({
                            "block_type": bt,
                            key: {"elements": item["elements"]},
                        })
            else:
                regular_batch.append(block)

        await _flush_regular()
        return {"ok": True, "blocks_added": total}

    async def _create_nested_list(self, doc_id: str, items: list[dict]) -> int:
        """Create nested list blocks using the Feishu descendant API.

        Args:
            items: flat list — [{"type": "bullet"|"ordered", "depth": N, "elements": [...]}]
                   Supports arbitrary nesting depth via stack-based reconstruction.

        Returns the number of top-level blocks created, 0 on failure.
        """
        top_level_ids, descendants = _build_descendant_payload(items)

        if not top_level_ids:
            return 0

        try:
            resp = await self._raw_request(
                "POST",
                f"/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/descendant",
                body={
                    "children_id": top_level_ids,
                    "index": -1,
                    "descendants": descendants,
                },
                params={"document_revision_id": "-1"},
            )
        except Exception as e:
            log.error("Descendant API failed (HTTP): %s | doc=%s", e, doc_id)
            return 0

        if resp.get("code") != 0:
            log.error("Descendant API failed (code %s): %s | doc=%s",
                      resp.get("code"), resp.get("msg"), doc_id)
            return 0

        return len(top_level_ids)

    async def _create_table_in_doc(self, doc_id: str, rows: list[list[str]]) -> str | None:
        """Create a native table in a Feishu document.

        Optimized 3-step flow (vs old N×M per-cell approach):
        1. Create empty table → 1 POST → get cell_ids
        2. Concurrent GET cell children → find text_block_ids (rate-limited)
        3. batch_update all cells → 1 PATCH (up to 200 ops)
        """
        import asyncio
        from platforms.feishu.blocks import _parse_inline

        row_count = len(rows)
        col_count = len(rows[0]) if rows else 0
        if row_count == 0 or col_count == 0:
            return None

        # Calculate proportional column widths (CJK-aware)
        TABLE_TOTAL_WIDTH = 700
        MIN_COL_WIDTH = 60
        max_lens = [0] * col_count
        for row in rows:
            for ci, cell in enumerate(row):
                w = sum(2 if ord(c) > 0x7F else 1 for c in (cell or ""))
                if w > max_lens[ci]:
                    max_lens[ci] = w
        max_lens = [max(length, 1) for length in max_lens]
        total = sum(max_lens)
        col_widths = [max(MIN_COL_WIDTH, int(TABLE_TOTAL_WIDTH * length / total))
                      for length in max_lens]

        # Step 1: Create empty table with calculated column widths
        resp = await self._raw_request(
            "POST",
            f"/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
            body={
                "children": [{
                    "block_type": 31,
                    "table": {
                        "property": {
                            "row_size": row_count,
                            "column_size": col_count,
                            "column_width": col_widths,
                            "header_row": True,
                        }
                    }
                }],
                "index": -1,
            },
            params={"document_revision_id": "-1"},
        )
        if resp.get("code") != 0:
            log.error("Create table failed: %s | rows=%d cols=%d doc=%s",
                      resp.get("msg"), row_count, col_count, doc_id)
            return None

        table_block = resp["data"]["children"][0]
        cell_ids = table_block.get("table", {}).get("cells", [])
        table_bid = table_block.get("block_id")
        if len(cell_ids) != row_count * col_count:
            log.warning("Table cell count mismatch: expected %d, got %d",
                        row_count * col_count, len(cell_ids))
            return table_bid

        # Step 2: Concurrent GET to find text_block_id inside each cell
        # Rate limit: semaphore caps concurrent requests (Feishu: 3 req/s/app)
        sem = asyncio.Semaphore(3)

        async def _get_text_block_id(cell_id: str) -> str | None:
            async with sem:
                child_resp = await self._raw_request(
                    "GET",
                    f"/open-apis/docx/v1/documents/{doc_id}/blocks/{cell_id}/children",
                    params={"document_revision_id": "-1"},
                )
                items = child_resp.get("data", {}).get("items", [])
                if items:
                    return items[0]["block_id"]
                return None

        text_block_ids = await asyncio.gather(
            *[_get_text_block_id(cid) for cid in cell_ids]
        )

        # Step 3: batch_update — write all cells in one PATCH
        # Build update requests: {block_id: {update_text_elements: {elements: [...]}}}
        update_requests = []
        idx = 0
        for ri, row in enumerate(rows):
            for ci, cell_text in enumerate(row):
                tb_id = text_block_ids[idx]
                idx += 1
                if not tb_id:
                    continue

                if ri == 0:  # header row = bold
                    elements = [{"text_run": {
                        "content": cell_text,
                        "text_element_style": {"bold": True},
                    }}]
                else:
                    elements = _parse_inline(cell_text) if cell_text else [
                        {"text_run": {"content": ""}}
                    ]

                update_requests.append({
                    "block_id": tb_id,
                    "update_text_elements": {"elements": elements},
                })

        if update_requests:
            # batch_update supports up to 200 operations
            for i in range(0, len(update_requests), 200):
                batch = update_requests[i:i + 200]
                batch_resp = await self._raw_request(
                    "PATCH",
                    f"/open-apis/docx/v1/documents/{doc_id}/blocks/batch_update",
                    body={"requests": batch},
                    params={"document_revision_id": "-1"},
                )
                if batch_resp.get("code") != 0:
                    log.warning("batch_update failed: %s | doc=%s, falling back to sequential",
                                batch_resp.get("msg"), doc_id)
                    # Fallback: sequential PATCH per cell
                    for req in batch:
                        await self._raw_request(
                            "PATCH",
                            f"/open-apis/docx/v1/documents/{doc_id}/blocks/{req['block_id']}",
                            body={"update_text_elements": req["update_text_elements"]},
                            params={"document_revision_id": "-1"},
                        )

        return table_bid

    async def search_documents(self, query: str, count: int = 10) -> list:
        """Search documents via Feishu suite search API (REST)."""
        try:
            resp = await self._raw_request(
                "POST",
                "/open-apis/suite/docs-api/search/object",
                body={"search_key": query, "count": min(count, 50), "offset": 0,
                      "owner_ids": [], "chat_ids": [], "docs_types": []},
            )
            if resp.get("code") != 0:
                return [{"error": f"{resp.get('code')}: {resp.get('msg')}"}]
            docs = resp.get("data", {}).get("docs_entities", [])
            return [
                {
                    "title": doc.get("title", ""),
                    "url": doc.get("url", ""),
                    "type": doc.get("docs_type", ""),
                    "doc_token": doc.get("docs_token", ""),
                }
                for doc in docs[:count]
            ]
        except Exception as e:
            return [{"error": str(e)}]

    async def list_comments(self, document_id: str) -> list:
        """List all comments on a document with their replies."""
        comments: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"file_type": "docx"}
            if page_token:
                params["page_token"] = page_token
            data = await self._raw_request(
                "GET",
                f"/open-apis/drive/v1/files/{document_id}/comments",
                params=params,
            )
            if data.get("code") != 0:
                return [{"error": f"{data.get('code')}: {data.get('msg')}"}]
            for item in data.get("data", {}).get("items", []):
                comments.append({
                    "comment_id": item.get("comment_id", ""),
                    "quote": item.get("quote", ""),
                    "is_resolved": item.get("is_resolved", False),
                    "replies": [
                        {
                            "reply_id": r.get("reply_id", ""),
                            "content": r.get("content", {}).get("text", "") if isinstance(r.get("content"), dict) else str(r.get("content", "")),
                            "user_id": r.get("user_id", ""),
                        }
                        for r in item.get("reply_list", {}).get("replies", [])
                    ],
                })
            if data.get("data", {}).get("has_more"):
                page_token = data["data"].get("page_token")
            else:
                break
        return comments

    async def reply_comment(self, document_id: str, comment_id: str, content: str) -> dict:
        """Reply to a comment on a document."""
        # Feishu API requires rich text structure, not plain string
        reply_body = {
            "content": {
                "elements": [
                    {
                        "type": "text_run",
                        "text_run": {"content": content},
                    }
                ]
            }
        }
        data = await self._raw_request(
            "POST",
            f"/open-apis/drive/v1/files/{document_id}/comments/{comment_id}/replies",
            body=reply_body,
            params={"file_type": "docx"},
        )
        if data.get("code") != 0:
            return {"error": f"{data.get('code')}: {data.get('msg')}"}
        return {"ok": True, "reply": data.get("data", {}).get("reply", {})}

    async def update_document(self, document_id: str, content: str) -> dict:
        """Replace all document content (keeps title).

        1. Fetch all blocks
        2. Delete child blocks of root (skip page/title block)
        3. Append new content
        """
        # Step 1: Get all blocks
        blocks_data = await self._raw_request(
            "GET",
            f"/open-apis/docx/v1/documents/{document_id}/blocks",
            params={"document_revision_id": "-1"},
        )
        if blocks_data.get("code") != 0:
            return {"error": f"get_blocks: {blocks_data.get('code')}: {blocks_data.get('msg')}"}

        items = blocks_data.get("data", {}).get("items", [])
        if not items:
            # No blocks at all, just append
            return await self.append_document(document_id, content)

        # The first block is the page/document block; its children are the content blocks
        page_block = items[0]
        child_ids = page_block.get("page", {}).get("body", {}).get("blocks", [])
        if not child_ids:
            # Try alternative structure
            child_ids = [
                b["block_id"] for b in items[1:]
                if b.get("parent_id") == document_id
            ]

        # Step 2: Delete all child blocks (batch delete, up to 50 per request)
        for i in range(0, len(child_ids), 50):
            batch = child_ids[i:i + 50]
            del_data = await self._raw_request(
                "DELETE",
                f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children/batch_delete",
                body={"start_index": 0, "end_index": len(batch)},
                params={"document_revision_id": "-1"},
            )
            if del_data.get("code") != 0:
                log.warning("delete_blocks partial failure: %s | doc=%s",
                            del_data.get("msg"), document_id)

        # Step 3: Append new content
        return await self.append_document(document_id, content)

    async def transfer_document_owner(self, document_id: str, new_owner_id: str) -> dict:
        """Transfer document ownership to another user (by open_id)."""
        data = await self._raw_request(
            "POST",
            f"/open-apis/drive/v1/permissions/{document_id}/members/transfer_owner",
            body={"member_type": "openid", "member_id": new_owner_id},
            params={"type": "docx"},
        )
        if data.get("code") != 0:
            return {"error": f"{data.get('code')}: {data.get('msg')}"}
        return {"ok": True, "new_owner": new_owner_id}

    # ── Tasks ─────────────────────────────────────────────────────

    _tasklist_guid: str | None = None

    async def _ensure_tasklist_guid(self) -> str:
        """Get or create the dedicated bot tasklist (cached after first call).

        Uses GET tasklists to find an existing "Bot Tasks" list before creating.
        Tasklist-scoped queries support tenant_access_token, unlike the global
        task list endpoint which requires user_access_token.
        """
        if self._tasklist_guid:
            return self._tasklist_guid

        client = self._ensure_client()
        from lark_oapi.api.task.v2 import (
            ListTasklistRequest,
            CreateTasklistRequest,
            InputTasklist,
        )

        # First, look for an existing "Bot Tasks" tasklist
        try:
            req = ListTasklistRequest.builder().page_size(50).build()
            resp = await client.task.v2.tasklist.alist(req)
            if resp.success() and resp.data and resp.data.items:
                for tl in resp.data.items:
                    if getattr(tl, "name", "") == "Bot Tasks":
                        self._tasklist_guid = tl.guid
                        log.info("Found existing tasklist: %s", self._tasklist_guid)
                        return self._tasklist_guid
        except Exception as e:
            log.warning("list_tasklists failed: %s", e)

        # Create a new "Bot Tasks" tasklist
        try:
            body = InputTasklist.builder().name("Bot Tasks").build()
            req = CreateTasklistRequest.builder().request_body(body).build()
            resp = await client.task.v2.tasklist.acreate(req)
            if not resp.success():
                raise RuntimeError(f"create_tasklist failed: {resp.code}: {resp.msg}")
            self._tasklist_guid = resp.data.tasklist.guid
            log.info("Created new tasklist: %s", self._tasklist_guid)
            return self._tasklist_guid
        except Exception as e:
            log.error("_ensure_tasklist_guid error: %s", e)
            raise

    async def create_task(
        self, title: str, due_date: str = "", description: str = ""
    ) -> dict:
        client = self._ensure_client()
        from lark_oapi.api.task.v2 import (
            CreateTaskRequest,
            Task,
            Due,
            AddTasklistTaskRequest,
            AddTasklistTaskRequestBody,
        )

        task_builder = Task.builder().summary(title)
        if description:
            task_builder = task_builder.description(description)
        if due_date:
            due = Due.builder().timestamp(_to_timestamp(due_date)).is_all_day(True).build()
            task_builder = task_builder.due(due)

        req = CreateTaskRequest.builder().request_body(task_builder.build()).build()
        resp = await client.task.v2.task.acreate(req)

        if not resp.success():
            log.error("create_task failed: %s %s", resp.code, resp.msg)
            return {"error": f"{resp.code}: {resp.msg}"}

        task_guid = resp.data.task.guid
        task_summary = resp.data.task.summary

        # Add to the dedicated tasklist so we can list via tenant_access_token
        try:
            tasklist_guid = await self._ensure_tasklist_guid()
            body = (
                AddTasklistTaskRequestBody.builder()
                .tasklist_guid(tasklist_guid)
                .build()
            )
            add_req = (
                AddTasklistTaskRequest.builder()
                .task_guid(task_guid)
                .request_body(body)
                .build()
            )
            add_resp = await client.task.v2.task.aadd_tasklist(add_req)
            if not add_resp.success():
                log.warning(
                    "add_task_to_tasklist failed: %s %s", add_resp.code, add_resp.msg
                )
        except Exception as e:
            log.warning("add_task_to_tasklist error: %s", e)

        return {"task_id": task_guid, "summary": task_summary}

    async def list_tasks(self, completed: bool = False) -> list:
        client = self._ensure_client()
        from lark_oapi.api.task.v2 import TasksTasklistRequest

        try:
            tasklist_guid = await self._ensure_tasklist_guid()
        except Exception as e:
            return [{"error": f"Cannot get tasklist: {e}"}]

        result = []
        page_token = ""
        while True:
            builder = (
                TasksTasklistRequest.builder()
                .tasklist_guid(tasklist_guid)
                .completed("true" if completed else "false")
                .page_size(50)
            )
            if page_token:
                builder = builder.page_token(page_token)
            req = builder.build()
            resp = await client.task.v2.tasklist.atasks(req)

            if not resp.success():
                log.error("list_tasks failed: %s %s", resp.code, resp.msg)
                if not result:
                    return [{"error": f"{resp.code}: {resp.msg}"}]
                break

            tasks = resp.data.items or []
            for t in tasks:
                is_done = bool(getattr(t, "completed_at", None))
                result.append({
                    "task_id": t.guid,
                    "summary": t.summary,
                    "completed": is_done,
                })

            page_token = getattr(resp.data, "page_token", "") or ""
            if not page_token:
                break
        return result

    async def get_task(self, task_id: str) -> dict:
        """Get a single task by ID."""
        client = self._ensure_client()
        from lark_oapi.api.task.v2 import GetTaskRequest

        req = GetTaskRequest.builder().task_guid(task_id).build()
        resp = await client.task.v2.task.aget(req)

        if not resp.success():
            return {"error": f"{resp.code}: {resp.msg}"}

        t = resp.data.task
        return {
            "task_id": t.guid,
            "summary": t.summary,
            "description": getattr(t, "description", "") or "",
            "completed": bool(getattr(t, "completed_at", None)),
            "due": getattr(getattr(t, "due", None), "timestamp", "") if getattr(t, "due", None) else "",
        }

    async def update_task(
        self, task_id: str, title: str = "", due_date: str = "", description: str = ""
    ) -> dict:
        """Update a task. Only non-empty fields are updated."""
        client = self._ensure_client()
        from lark_oapi.api.task.v2 import PatchTaskRequest, Task, Due

        task_builder = Task.builder()
        if title:
            task_builder = task_builder.summary(title)
        if description:
            task_builder = task_builder.description(description)
        if due_date:
            due = Due.builder().timestamp(_to_timestamp(due_date)).is_all_day(True).build()
            task_builder = task_builder.due(due)

        req = (
            PatchTaskRequest.builder()
            .task_guid(task_id)
            .request_body(task_builder.build())
            .build()
        )
        resp = await client.task.v2.task.apatch(req)

        if not resp.success():
            return {"error": f"{resp.code}: {resp.msg}"}
        return {"ok": True, "task_id": task_id}

    async def delete_task(self, task_id: str) -> dict:
        """Delete a task."""
        client = self._ensure_client()
        from lark_oapi.api.task.v2 import DeleteTaskRequest

        req = DeleteTaskRequest.builder().task_guid(task_id).build()
        resp = await client.task.v2.task.adelete(req)

        if not resp.success():
            return {"error": f"{resp.code}: {resp.msg}"}
        return {"ok": True, "task_id": task_id}

    async def complete_task(self, task_id: str) -> dict:
        client = self._ensure_client()
        from lark_oapi.api.task.v2 import PatchTaskRequest, Task

        completed_task = Task.builder().completed_at(str(int(time.time()))).build()
        req = (
            PatchTaskRequest.builder()
            .task_guid(task_id)
            .request_body(completed_task)
            .build()
        )
        resp = await client.task.v2.task.apatch(req)

        if not resp.success():
            return {"error": f"{resp.code}: {resp.msg}"}
        return {"ok": True, "task_id": task_id}

    # ── Messages ──────────────────────────────────────────────────

    async def send_message(self, chat_id: str, text: str) -> dict:
        client = self._ensure_client()
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        resp = await client.im.v1.message.acreate(req)

        if not resp.success():
            return {"error": f"{resp.code}: {resp.msg}"}
        return {"message_id": resp.data.message_id}

    # ── Calendar ──────────────────────────────────────────────────

    _primary_calendar_id: str | None = None

    async def _get_primary_calendar_id(self) -> str:
        """Get the bot's primary calendar ID (cached after first call)."""
        if self._primary_calendar_id:
            return self._primary_calendar_id
        client = self._ensure_client()
        from lark_oapi.api.calendar.v4 import PrimaryCalendarRequest
        req = PrimaryCalendarRequest.builder().build()
        try:
            resp = await client.calendar.v4.calendar.aprimary(req)
            if resp.success() and resp.data and resp.data.calendars:
                self._primary_calendar_id = resp.data.calendars[0].calendar.calendar_id
                return self._primary_calendar_id
        except Exception:
            pass
        # Fallback: list calendars and pick the first one
        from lark_oapi.api.calendar.v4 import ListCalendarRequest
        req2 = ListCalendarRequest.builder().build()
        try:
            resp2 = await client.calendar.v4.calendar.alist(req2)
            if resp2.success() and resp2.data and resp2.data.calendar_list:
                self._primary_calendar_id = resp2.data.calendar_list[0].calendar_id
                return self._primary_calendar_id
        except Exception:
            pass
        raise RuntimeError("Cannot determine primary calendar ID")

    async def list_events(self, days: int = 7) -> list:
        client = self._ensure_client()
        from lark_oapi.api.calendar.v4 import ListCalendarEventRequest

        cal_id = await self._get_primary_calendar_id()
        now = int(time.time())
        end = now + days * 86400

        req = (
            ListCalendarEventRequest.builder()
            .calendar_id(cal_id)
            .start_time(str(now))
            .end_time(str(end))
            .build()
        )

        try:
            resp = await client.calendar.v4.calendar_event.alist(req)
            if not resp.success():
                return [{"error": f"{resp.code}: {resp.msg}"}]
            events = resp.data.items or []
            return [
                {
                    "event_id": e.event_id,
                    "summary": e.summary,
                    "start_time": getattr(e.start_time, "timestamp", ""),
                    "end_time": getattr(e.end_time, "timestamp", ""),
                }
                for e in events
            ]
        except Exception as e:
            return [{"error": str(e)}]

    async def create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
        attendees: list[str] | None = None,
    ) -> dict:
        client = self._ensure_client()
        from lark_oapi.api.calendar.v4 import (
            CreateCalendarEventRequest,
            CalendarEvent,
            TimeInfo,
        )

        cal_id = await self._get_primary_calendar_id()

        start_ts = _to_timestamp(start_time)
        end_ts = _to_timestamp(end_time)
        log.info("create_event: summary=%r, start=%r→%s, end=%r→%s, attendees=%s",
                 summary, start_time, start_ts, end_time, end_ts,
                 attendees if attendees else "none")

        event_builder = CalendarEvent.builder().summary(summary)
        event_builder.start_time(TimeInfo.builder().timestamp(start_ts).build())
        event_builder.end_time(TimeInfo.builder().timestamp(end_ts).build())
        if description:
            event_builder.description(description)

        req = (
            CreateCalendarEventRequest.builder()
            .calendar_id(cal_id)
            .request_body(event_builder.build())
            .build()
        )

        try:
            resp = await client.calendar.v4.calendar_event.acreate(req)
            if not resp.success():
                log.error("create_event failed: %s %s", resp.code, resp.msg)
                return {"error": f"{resp.code}: {resp.msg}"}

            event_id = resp.data.event.event_id
            result: dict[str, Any] = {"event_id": event_id, "summary": summary}

            # Add attendees if provided
            if attendees:
                from lark_oapi.api.calendar.v4 import (
                    CreateCalendarEventAttendeeRequest,
                    CreateCalendarEventAttendeeRequestBody,
                    CalendarEventAttendee,
                )
                # Normalize: accept both ["ou_xxx"] and [{"user_id": "ou_xxx"}]
                normalized: list[str] = []
                for item in attendees:
                    if isinstance(item, str):
                        normalized.append(item)
                    elif isinstance(item, dict):
                        uid = item.get("user_id") or item.get("open_id") or item.get("id", "")
                        if uid:
                            normalized.append(uid)
                if not normalized:
                    log.warning("attendees provided but no valid open_ids extracted: %s", attendees)
                    result["attendees_error"] = "no valid open_ids in attendees"
                else:
                    attendee_list = [
                        CalendarEventAttendee.builder()
                        .type("user")
                        .user_id(uid)
                        .build()
                        for uid in normalized
                    ]
                    att_req = (
                        CreateCalendarEventAttendeeRequest.builder()
                        .calendar_id(cal_id)
                        .event_id(event_id)
                        .user_id_type("open_id")
                        .request_body(
                            CreateCalendarEventAttendeeRequestBody.builder()
                            .attendees(attendee_list)
                            .build()
                        )
                        .build()
                    )
                    att_resp = await client.calendar.v4.calendar_event_attendee.acreate(att_req)
                    if not att_resp.success():
                        log.warning("add_attendees failed: %s %s", att_resp.code, att_resp.msg)
                        result["attendees_error"] = f"{att_resp.code}: {att_resp.msg}"
                    else:
                        result["attendees_added"] = len(normalized)

            return result
        except Exception as e:
            return {"error": str(e)}

    async def update_event(
        self,
        event_id: str,
        summary: str = "",
        start_time: str = "",
        end_time: str = "",
        description: str = "",
    ) -> dict:
        """Update a calendar event. Only non-empty fields are updated."""
        client = self._ensure_client()
        from lark_oapi.api.calendar.v4 import (
            PatchCalendarEventRequest,
            CalendarEvent,
            TimeInfo,
        )

        cal_id = await self._get_primary_calendar_id()

        event_builder = CalendarEvent.builder()
        if summary:
            event_builder = event_builder.summary(summary)
        if start_time:
            event_builder = event_builder.start_time(
                TimeInfo.builder().timestamp(_to_timestamp(start_time)).build()
            )
        if end_time:
            event_builder = event_builder.end_time(
                TimeInfo.builder().timestamp(_to_timestamp(end_time)).build()
            )
        if description:
            event_builder = event_builder.description(description)

        req = (
            PatchCalendarEventRequest.builder()
            .calendar_id(cal_id)
            .event_id(event_id)
            .request_body(event_builder.build())
            .build()
        )

        try:
            resp = await client.calendar.v4.calendar_event.apatch(req)
            if not resp.success():
                log.error("update_event failed: %s %s", resp.code, resp.msg)
                return {"error": f"{resp.code}: {resp.msg}"}
            return {"ok": True, "event_id": event_id}
        except Exception as e:
            return {"error": str(e)}

    async def delete_event(self, event_id: str) -> dict:
        """Delete a calendar event."""
        client = self._ensure_client()
        from lark_oapi.api.calendar.v4 import DeleteCalendarEventRequest

        cal_id = await self._get_primary_calendar_id()

        req = (
            DeleteCalendarEventRequest.builder()
            .calendar_id(cal_id)
            .event_id(event_id)
            .build()
        )

        try:
            resp = await client.calendar.v4.calendar_event.adelete(req)
            if not resp.success():
                log.error("delete_event failed: %s %s", resp.code, resp.msg)
                return {"error": f"{resp.code}: {resp.msg}"}
            return {"ok": True, "event_id": event_id}
        except Exception as e:
            return {"error": str(e)}

    async def freebusy(
        self,
        start_time: str,
        end_time: str,
        user_ids: list[str] | None = None,
    ) -> dict:
        """Query free/busy status for a time range.

        Args:
            start_time: Start time (ISO 8601, datetime string, or Unix timestamp)
            end_time: End time (same formats)
            user_ids: Optional list of open_ids to query. If empty, queries the bot's own calendar.

        Returns:
            dict with "busy_list" (list of busy intervals) or "error".
        """
        # freebusy API requires RFC3339 format, not unix timestamps
        body: dict[str, Any] = {
            "time_min": _to_rfc3339(start_time),
            "time_max": _to_rfc3339(end_time),
        }
        if user_ids:
            body["user_id"] = user_ids[0]

        # Try SDK first
        try:
            client = self._ensure_client()
            from lark_oapi.api.calendar.v4 import (
                FreebusyListRequest,
                FreebusyListRequestBody,
            )
            from lark_oapi.api.calendar.v4.model import UserId

            req_body_builder = FreebusyListRequestBody.builder()
            req_body_builder = req_body_builder.time_min(body["time_min"])
            req_body_builder = req_body_builder.time_max(body["time_max"])
            if user_ids:
                uid_obj = UserId.builder().open_id(user_ids[0]).build()
                req_body_builder = req_body_builder.user_id(uid_obj)

            req = (
                FreebusyListRequest.builder()
                .request_body(req_body_builder.build())
                .build()
            )
            resp = await client.calendar.v4.freebusy.alist(req)
            if resp.success():
                freebusy_list = resp.data.freebusy_list or []
                return {
                    "busy_list": [
                        {
                            "start_time": getattr(fb, "start_time", ""),
                            "end_time": getattr(fb, "end_time", ""),
                        }
                        for fb in freebusy_list
                    ]
                }
            # SDK call failed, fall through to raw HTTP
            log.warning("freebusy SDK failed: %s %s, trying raw HTTP", resp.code, resp.msg)
        except (ImportError, AttributeError) as e:
            log.info("freebusy SDK class not available (%s), using raw HTTP", e)

        # Fallback: raw HTTP
        data = await self._raw_request(
            "POST",
            "/open-apis/calendar/v4/freebusy/list",
            body=body,
            params={"user_id_type": "open_id"} if user_ids else None,
        )
        if data.get("code") != 0:
            return {"error": f"{data.get('code')}: {data.get('msg')}"}
        freebusy_list = data.get("data", {}).get("freebusy_list", [])
        return {
            "busy_list": [
                {
                    "start_time": fb.get("start_time", ""),
                    "end_time": fb.get("end_time", ""),
                }
                for fb in freebusy_list
            ]
        }

    # ── Calendar Attendee CRUD ─────────────────────────────────

    async def list_event_attendees(self, event_id: str) -> list[dict]:
        """List attendees for a calendar event."""
        calendar_id = await self._get_primary_calendar_id()
        data = await self._raw_request(
            "GET",
            f"/open-apis/calendar/v4/calendars/{calendar_id}/events/{event_id}/attendees",
            params={"page_size": 50, "user_id_type": "open_id"},
        )
        items = data.get("data", {}).get("items", [])
        return [
            {
                "attendee_id": a.get("attendee_id", ""),
                "user_id": a.get("user_id", ""),
                "display_name": a.get("display_name", ""),
                "status": a.get("rsvp_status", "needs_action"),
                "type": a.get("type", "user"),
            }
            for a in items
        ]

    async def add_event_attendees(self, event_id: str, attendee_ids: list[str]) -> dict:
        """Add attendees to an existing calendar event.

        Args:
            event_id: Calendar event ID
            attendee_ids: List of open_id strings
        """
        calendar_id = await self._get_primary_calendar_id()
        attendees = [{"user_id": uid, "type": "user"} for uid in attendee_ids]
        data = await self._raw_request(
            "POST",
            f"/open-apis/calendar/v4/calendars/{calendar_id}/events/{event_id}/attendees",
            body={"attendees": attendees},
            params={"user_id_type": "open_id"},
        )
        if data.get("code") != 0:
            return {"error": f"{data.get('code')}: {data.get('msg')}"}
        return {"added": len(attendee_ids), "event_id": event_id}

    async def remove_event_attendees(self, event_id: str, attendee_ids: list[str]) -> dict:
        """Remove attendees from a calendar event.

        Args:
            event_id: Calendar event ID
            attendee_ids: List of attendee_id strings (from list_event_attendees)
        """
        calendar_id = await self._get_primary_calendar_id()
        data = await self._raw_request(
            "POST",
            f"/open-apis/calendar/v4/calendars/{calendar_id}/events/{event_id}/attendees/batch_delete",
            body={"attendee_ids": attendee_ids},
        )
        if data.get("code") != 0:
            return {"error": f"{data.get('code')}: {data.get('msg')}"}
        return {"removed": len(attendee_ids), "event_id": event_id}

    async def get_merged_forward_messages(self, message_id: str) -> list[dict]:
        """Get sub-messages from a merged forward message.

        Fetches the message, parses the merged content to extract
        individual sub-messages, and returns their text content.

        Args:
            message_id: The merged forward message ID.

        Returns:
            List of dicts with "msg_type" and "content" for each sub-message.
        """
        # Step 1: Get the merged forward message itself
        data = await self._raw_request(
            "GET",
            f"/open-apis/im/v1/messages/{message_id}",
        )
        if data.get("code") != 0:
            return [{"error": f"{data.get('code')}: {data.get('msg')}"}]

        items = data.get("data", {}).get("items", [])
        if not items:
            return [{"error": "Message not found"}]

        msg = items[0]
        msg_type = msg.get("msg_type", "")

        # If not a merge_forward, return the message itself
        if msg_type != "merge_forward":
            return [{"msg_type": msg_type, "content": msg.get("body", {}).get("content", "")}]

        # Step 2: Parse the merged forward content for sub-message IDs
        content_str = msg.get("body", {}).get("content", "")
        try:
            content = json.loads(content_str) if isinstance(content_str, str) else content_str
        except (json.JSONDecodeError, TypeError):
            content = {}

        # Merged forward content contains message_id list
        sub_message_ids = content.get("message_id_list", [])
        if not sub_message_ids:
            # Try alternative: some versions put messages inline
            messages = content.get("messages", [])
            if messages:
                return [
                    {
                        "msg_type": m.get("msg_type", "unknown"),
                        "content": m.get("body", {}).get("content", "")
                        if isinstance(m.get("body"), dict)
                        else str(m.get("content", "")),
                    }
                    for m in messages
                ]
            return [{"error": "No sub-messages found in merged forward"}]

        # Step 3: Fetch each sub-message
        results: list[dict] = []
        for sub_id in sub_message_ids:
            sub_data = await self._raw_request(
                "GET",
                f"/open-apis/im/v1/messages/{sub_id}",
            )
            if sub_data.get("code") != 0:
                results.append({"msg_type": "error", "content": f"Failed to fetch {sub_id}"})
                continue
            sub_items = sub_data.get("data", {}).get("items", [])
            if sub_items:
                sub_msg = sub_items[0]
                results.append({
                    "msg_type": sub_msg.get("msg_type", "unknown"),
                    "content": sub_msg.get("body", {}).get("content", ""),
                })
        return results

    # ── Media download ─────────────────────────────────────────────

    async def download_resource(
        self, message_id: str, file_key: str, resource_type: str = "image"
    ) -> bytes | None:
        """Download an image or file from a Feishu message.

        Args:
            message_id: The message containing the resource
            file_key: The image_key or file_key
            resource_type: "image" or "file"

        Returns: raw bytes or None on failure.
        """
        token = await self._get_tenant_token()
        url = (
            f"{self.domain}/open-apis/im/v1/messages/{message_id}"
            f"/resources/{file_key}?type={resource_type}"
        )
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=60,
                )
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith(
                    ("image/", "application/", "audio/", "video/")
                ):
                    return resp.content
                log.warning(
                    "Download resource failed: status=%s type=%s",
                    resp.status_code,
                    resp.headers.get("content-type"),
                )
                return None
        except Exception as e:
            log.warning("Download resource error: %s", e)
            return None

    # ── Bitable ────────────────────────────────────────────────────

    async def create_bitable(self, name: str, folder_token: str = "") -> dict:
        """Create a new Bitable app."""
        body: dict[str, Any] = {"name": name}
        if folder_token:
            body["folder_token"] = folder_token
        try:
            resp = await self._raw_request(
                "POST", "/open-apis/bitable/v1/apps", body=body,
            )
            app = resp.get("data", {}).get("app", {})
            return {
                "app_token": app.get("app_token", ""),
                "name": app.get("name", name),
                "url": app.get("url", ""),
            }
        except Exception as e:
            return {"error": str(e)}

    async def list_bitable_tables(self, app_token: str) -> list:
        """List all tables in a Bitable app."""
        token = await self._get_tenant_token()
        url = f"{self.domain}/open-apis/bitable/v1/apps/{app_token}/tables"
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    url, headers={"Authorization": f"Bearer {token}"}, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return [{"error": f"{data.get('code')}: {data.get('msg')}"}]
                items = data.get("data", {}).get("items", [])
                return [
                    {"table_id": t["table_id"], "name": t.get("name", "")}
                    for t in items
                ]
        except Exception as e:
            return [{"error": str(e)}]

    async def query_bitable_records(
        self,
        app_token: str,
        table_id: str,
        filter_expr: str = "",
        page_size: int = 20,
    ) -> list:
        """Query records from a Bitable table."""
        token = await self._get_tenant_token()
        url = (
            f"{self.domain}/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/records/search"
        )
        body: dict[str, Any] = {"page_size": min(page_size, 100)}
        if filter_expr:
            body["filter"] = {"conjunction": "and", "conditions": []}
            # Pass raw filter string — caller formats it
            body["filter"] = filter_expr
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    url, headers={"Authorization": f"Bearer {token}"},
                    json=body, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return [{"error": f"{data.get('code')}: {data.get('msg')}"}]
                items = data.get("data", {}).get("items", [])
                return [
                    {"record_id": r["record_id"], "fields": r.get("fields", {})}
                    for r in items
                ]
        except Exception as e:
            return [{"error": str(e)}]

    async def add_bitable_record(
        self, app_token: str, table_id: str, fields: dict
    ) -> dict:
        """Add a record to a Bitable table."""
        token = await self._get_tenant_token()
        url = (
            f"{self.domain}/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/records"
        )
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    url, headers={"Authorization": f"Bearer {token}"},
                    json={"fields": fields}, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return {"error": f"{data.get('code')}: {data.get('msg')}"}
                record = data.get("data", {}).get("record", {})
                return {"record_id": record.get("record_id", ""), "ok": True}
        except Exception as e:
            return {"error": str(e)}

    async def update_bitable_record(
        self, app_token: str, table_id: str, record_id: str, fields: dict
    ) -> dict:
        """Update a Bitable record."""
        token = await self._get_tenant_token()
        url = (
            f"{self.domain}/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/records/{record_id}"
        )
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.put(
                    url, headers={"Authorization": f"Bearer {token}"},
                    json={"fields": fields}, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return {"error": f"{data.get('code')}: {data.get('msg')}"}
                return {"record_id": record_id, "ok": True}
        except Exception as e:
            return {"error": str(e)}

    async def delete_bitable_record(
        self, app_token: str, table_id: str, record_id: str
    ) -> dict:
        """Delete a Bitable record."""
        token = await self._get_tenant_token()
        url = (
            f"{self.domain}/open-apis/bitable/v1/apps/{app_token}"
            f"/tables/{table_id}/records/{record_id}"
        )
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.delete(
                    url, headers={"Authorization": f"Bearer {token}"}, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return {"error": f"{data.get('code')}: {data.get('msg')}"}
                return {"record_id": record_id, "deleted": True}
        except Exception as e:
            return {"error": str(e)}

    # ── Spreadsheet ────────────────────────────────────────────────

    async def create_spreadsheet(self, title: str, folder_token: str = "") -> dict:
        """Create a new spreadsheet."""
        body: dict[str, Any] = {"title": title}
        if folder_token:
            body["folder_token"] = folder_token
        try:
            resp = await self._raw_request(
                "POST", "/open-apis/sheets/v3/spreadsheets", body=body,
            )
            sheet = resp.get("data", {}).get("spreadsheet", {})
            return {
                "spreadsheet_token": sheet.get("spreadsheet_token", ""),
                "title": sheet.get("title", title),
                "url": sheet.get("url", ""),
            }
        except Exception as e:
            return {"error": str(e)}

    async def get_spreadsheet_info(self, spreadsheet_token: str) -> dict:
        """Get spreadsheet metadata and worksheet list."""
        token = await self._get_tenant_token()
        url = f"{self.domain}/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query"
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    url, headers={"Authorization": f"Bearer {token}"}, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return {"error": f"{data.get('code')}: {data.get('msg')}"}
                sheets = data.get("data", {}).get("sheets", [])
                return {
                    "spreadsheet_token": spreadsheet_token,
                    "sheets": [
                        {"sheet_id": s["sheet_id"], "title": s.get("title", "")}
                        for s in sheets
                    ],
                }
        except Exception as e:
            return {"error": str(e)}

    async def read_sheet_range(
        self, spreadsheet_token: str, sheet_id: str, range_str: str
    ) -> list:
        """Read cell values from a spreadsheet range."""
        token = await self._get_tenant_token()
        full_range = f"{sheet_id}!{range_str}"
        url = (
            f"{self.domain}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}"
            f"/values/{full_range}"
        )
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    url, headers={"Authorization": f"Bearer {token}"}, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return [{"error": f"{data.get('code')}: {data.get('msg')}"}]
                return data.get("data", {}).get("valueRange", {}).get("values", [])
        except Exception as e:
            return [{"error": str(e)}]

    async def write_sheet_range(
        self, spreadsheet_token: str, sheet_id: str, range_str: str, values: list
    ) -> dict:
        """Write values to a spreadsheet range."""
        token = await self._get_tenant_token()
        full_range = f"{sheet_id}!{range_str}"
        url = (
            f"{self.domain}/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}"
            f"/values"
        )
        body = {
            "valueRange": {
                "range": full_range,
                "values": values,
            }
        }
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.put(
                    url, headers={"Authorization": f"Bearer {token}"},
                    json=body, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return {"error": f"{data.get('code')}: {data.get('msg')}"}
                return {"ok": True, "range": full_range}
        except Exception as e:
            return {"error": str(e)}

    # ── Drive ──────────────────────────────────────────────────────

    async def list_drive_files(
        self, folder_token: str = "", page_size: int = 20
    ) -> list:
        """List files in a Drive folder."""
        token = await self._get_tenant_token()
        url = f"{self.domain}/open-apis/drive/v1/files"
        params: dict[str, Any] = {"page_size": min(page_size, 50)}
        if folder_token:
            params["folder_token"] = folder_token
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    url, headers={"Authorization": f"Bearer {token}"},
                    params=params, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return [{"error": f"{data.get('code')}: {data.get('msg')}"}]
                items = data.get("data", {}).get("files", [])
                return [
                    {
                        "token": f.get("token", ""),
                        "name": f.get("name", ""),
                        "type": f.get("type", ""),
                        "url": f.get("url", ""),
                    }
                    for f in items
                ]
        except Exception as e:
            return [{"error": str(e)}]

    async def search_drive(self, query: str, count: int = 10) -> list:
        """Search files by name in Drive."""
        token = await self._get_tenant_token()
        url = f"{self.domain}/open-apis/suite/docs-api/search/object"
        body = {"search_key": query, "count": min(count, 50), "docs_types": []}
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    url, headers={"Authorization": f"Bearer {token}"},
                    json=body, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return [{"error": f"{data.get('code')}: {data.get('msg')}"}]
                items = data.get("data", {}).get("docs_entities", [])
                return [
                    {
                        "token": d.get("docs_token", ""),
                        "title": d.get("title", ""),
                        "type": d.get("docs_type", ""),
                        "url": d.get("url", ""),
                    }
                    for d in items
                ]
        except Exception as e:
            return [{"error": str(e)}]

    async def create_drive_folder(self, name: str, parent_token: str) -> dict:
        """Create a folder in Drive. parent_token is required."""
        token = await self._get_tenant_token()
        url = f"{self.domain}/open-apis/drive/v1/files/create_folder"
        body: dict[str, Any] = {"name": name, "folder_token": parent_token}
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    url, headers={"Authorization": f"Bearer {token}"},
                    json=body, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return {"error": f"{data.get('code')}: {data.get('msg')}"}
                return {"token": data.get("data", {}).get("token", ""), "ok": True}
        except Exception as e:
            return {"error": str(e)}

    async def move_drive_file(self, file_token: str, target_folder_token: str) -> dict:
        """Move a file/folder to another folder."""
        token = await self._get_tenant_token()
        url = f"{self.domain}/open-apis/drive/v1/files/{file_token}/move"
        body = {"folder_token": target_folder_token}
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    url, headers={"Authorization": f"Bearer {token}"},
                    json=body, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return {"error": f"{data.get('code')}: {data.get('msg')}"}
                return {"ok": True}
        except Exception as e:
            return {"error": str(e)}

    async def delete_drive_file(
        self, file_token: str, file_type: str = "docx",
    ) -> dict:
        """Delete a file from Drive (moves to trash, recoverable within 30 days).

        file_type: docx, sheet, bitable, mindnote, file, folder, etc.
        """
        token = await self._get_tenant_token()
        url = f"{self.domain}/open-apis/drive/v1/files/{file_token}"
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.delete(
                    url, headers={"Authorization": f"Bearer {token}"},
                    params={"type": file_type}, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return {"error": f"{data.get('code')}: {data.get('msg')}"}
                return {"ok": True, "task_id": data.get("data", {}).get("task_id", "")}
        except Exception as e:
            return {"error": str(e)}

    # ── Permission ─────────────────────────────────────────────────

    async def list_collaborators(
        self, doc_token: str, doc_type: str = "docx",
    ) -> list:
        """List all collaborators on a document."""
        try:
            resp = await self._raw_request(
                "GET",
                f"/open-apis/drive/v1/permissions/{doc_token}/members",
                params={"type": doc_type},
            )
            if resp.get("code") != 0:
                return [{"error": f"{resp.get('code')}: {resp.get('msg')}"}]
            items = resp.get("data", {}).get("items", [])
            return [
                {
                    "member_type": m.get("member_type", ""),
                    "member_id": m.get("member_id", ""),
                    "perm": m.get("perm", ""),
                    "name": m.get("name", ""),
                }
                for m in items
            ] if items else []
        except Exception as e:
            return [{"error": str(e)}]

    async def get_public_sharing(
        self, doc_token: str, doc_type: str = "docx",
    ) -> dict:
        """Get public sharing settings for a document."""
        try:
            resp = await self._raw_request(
                "GET",
                f"/open-apis/drive/v1/permissions/{doc_token}/public",
                params={"type": doc_type},
            )
            if resp.get("code") != 0:
                return {"error": f"{resp.get('code')}: {resp.get('msg')}"}
            ps = resp.get("data", {}).get("permission_public", {})
            return {
                "link_share_entity": ps.get("link_share_entity", ""),
                "external_access_entity": ps.get("external_access_entity", ""),
                "invite_external": ps.get("invite_external", False),
                "security_entity": ps.get("security_entity", ""),
            }
        except Exception as e:
            return {"error": str(e)}

    async def add_collaborator(
        self,
        doc_token: str,
        member_id: str,
        perm: str = "full_access",
        member_type: str = "openid",
        doc_type: str = "docx",
    ) -> dict:
        """Add a collaborator to a document."""
        token = await self._get_tenant_token()
        url = (
            f"{self.domain}/open-apis/drive/v1/permissions/{doc_token}"
            f"/members?type={doc_type}"
        )
        body = {
            "member_type": member_type,
            "member_id": member_id,
            "perm": perm,
        }
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    url, headers={"Authorization": f"Bearer {token}"},
                    json=body, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return {"error": f"{data.get('code')}: {data.get('msg')}"}
                return {"ok": True, "member_id": member_id, "perm": perm}
        except Exception as e:
            return {"error": str(e)}

    async def remove_collaborator(
        self,
        doc_token: str,
        member_id: str,
        member_type: str = "openid",
        doc_type: str = "docx",
    ) -> dict:
        """Remove a collaborator from a document."""
        token = await self._get_tenant_token()
        url = (
            f"{self.domain}/open-apis/drive/v1/permissions/{doc_token}"
            f"/members/{member_id}?type={doc_type}&member_type={member_type}"
        )
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.delete(
                    url, headers={"Authorization": f"Bearer {token}"}, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return {"error": f"{data.get('code')}: {data.get('msg')}"}
                return {"ok": True, "removed": member_id}
        except Exception as e:
            return {"error": str(e)}

    # ── Document: section replace ───────────────────────────────

    async def get_document_blocks(self, document_id: str) -> list[dict]:
        """Get all blocks from a document, handling pagination."""
        all_items: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"document_revision_id": "-1", "page_size": "500"}
            if page_token:
                params["page_token"] = page_token
            data = await self._raw_request(
                "GET",
                f"/open-apis/docx/v1/documents/{document_id}/blocks",
                params=params,
            )
            if data.get("code") != 0:
                log.error("get_document_blocks failed: %s | doc=%s", data.get("msg"), document_id)
                break
            all_items.extend(data.get("data", {}).get("items", []))
            if data.get("data", {}).get("has_more"):
                page_token = data["data"].get("page_token")
            else:
                break
        return all_items

    def _block_heading_level(self, block: dict) -> int | None:
        """Return heading level (1-6) if block is a heading, else None.

        Block types 3-8 map to heading1-heading6.
        """
        bt = block.get("block_type")
        if bt is not None and 3 <= bt <= 8:
            return bt - 2  # type 3 → level 1, type 4 → level 2, ...
        return None

    def _block_text_content(self, block: dict) -> str:
        """Extract plain text from a block's elements."""
        for key in ("heading1", "heading2", "heading3", "heading4", "heading5", "heading6", "text"):
            section = block.get(key)
            if section and "elements" in section:
                parts: list[str] = []
                for el in section["elements"]:
                    tr = el.get("text_run")
                    if tr:
                        parts.append(tr.get("content", ""))
                return "".join(parts)
        return ""

    async def replace_section(self, document_id: str, heading_title: str, new_content: str) -> dict:
        """Replace a document section identified by heading title.

        Finds the heading block matching heading_title, deletes all blocks from
        that heading to the next heading of the same or higher level (or end of
        document), then appends new_content at the end of the document.

        Returns: {"ok": True, "deleted": N, "appended": dict} or {"error": ...}
        """
        blocks = await self.get_document_blocks(document_id)
        if not blocks:
            return {"error": "No blocks found in document"}

        # Build ordered list of content blocks (skip page block at index 0)
        page_block = blocks[0]
        child_ids = page_block.get("page", {}).get("body", {}).get("blocks", [])
        if not child_ids:
            child_ids = [
                b["block_id"] for b in blocks[1:]
                if b.get("parent_id") == document_id
            ]

        # Create block_id → block lookup
        block_map = {b["block_id"]: b for b in blocks}

        # Find the heading block matching heading_title
        start_idx: int | None = None
        section_level: int | None = None
        for i, bid in enumerate(child_ids):
            b = block_map.get(bid)
            if not b:
                continue
            level = self._block_heading_level(b)
            if level is not None:
                text = self._block_text_content(b).strip()
                if text == heading_title.strip():
                    start_idx = i
                    section_level = level
                    break

        if start_idx is None:
            return {"error": f"Heading '{heading_title}' not found"}

        # Find the end of the section (next heading of same or higher level)
        end_idx = len(child_ids)  # default: end of document
        for i in range(start_idx + 1, len(child_ids)):
            b = block_map.get(child_ids[i])
            if not b:
                continue
            level = self._block_heading_level(b)
            if level is not None and level <= section_level:
                end_idx = i
                break

        # Collect block IDs to delete
        to_delete = child_ids[start_idx:end_idx]
        if not to_delete:
            return {"error": "No blocks to delete in section"}

        # Delete blocks using batch_delete (index-based on the page block's children)
        # After each batch delete, indices shift, so always delete from start_idx.
        remaining = len(to_delete)
        deleted = 0
        while remaining > 0:
            batch_size = min(remaining, 50)
            del_data = await self._raw_request(
                "DELETE",
                f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children/batch_delete",
                body={"start_index": start_idx, "end_index": start_idx + batch_size},
                params={"document_revision_id": "-1"},
            )
            if del_data.get("code") != 0:
                log.warning("replace_section delete failed at batch: %s | doc=%s",
                            del_data.get("msg"), document_id)
                break
            deleted += batch_size
            remaining -= batch_size

        # Append new content
        appended = await self.append_document(document_id, new_content)
        return {"ok": True, "deleted": deleted, "appended": appended}

    # ── Tasks: assign/unassign + sections + snapshot ──────────

    async def assign_task(self, task_id: str, open_ids: list[str]) -> dict:
        """Assign users to a task.

        Args:
            task_id: The task GUID
            open_ids: List of user open_ids to assign
        """
        members = [
            {"id": oid, "type": "user", "role": "assignee"}
            for oid in open_ids
        ]
        data = await self._raw_request(
            "POST",
            f"/open-apis/task/v2/tasks/{task_id}/add_members",
            body={"members": members},
        )
        if data.get("code") != 0:
            return {"error": f"{data.get('code')}: {data.get('msg')}"}
        return {"ok": True, "task_id": task_id, "assigned": open_ids}

    async def unassign_task(self, task_id: str, open_ids: list[str]) -> dict:
        """Remove users from a task.

        Args:
            task_id: The task GUID
            open_ids: List of user open_ids to unassign
        """
        members = [
            {"id": oid, "type": "user", "role": "assignee"}
            for oid in open_ids
        ]
        data = await self._raw_request(
            "POST",
            f"/open-apis/task/v2/tasks/{task_id}/remove_members",
            body={"members": members},
        )
        if data.get("code") != 0:
            return {"error": f"{data.get('code')}: {data.get('msg')}"}
        return {"ok": True, "task_id": task_id, "unassigned": open_ids}

    async def create_task_section(self, name: str) -> dict:
        """Create a section in the bot's tasklist.

        Args:
            name: Section name
        """
        tasklist_guid = await self._ensure_tasklist_guid()
        data = await self._raw_request(
            "POST",
            f"/open-apis/task/v2/tasklists/{tasklist_guid}/sections",
            body={"name": name},
        )
        if data.get("code") != 0:
            return {"error": f"{data.get('code')}: {data.get('msg')}"}
        section = data.get("data", {}).get("section", {})
        return {
            "ok": True,
            "section_guid": section.get("guid", ""),
            "name": section.get("name", name),
        }

    async def list_task_sections(self) -> list:
        """List all sections in the bot's tasklist."""
        tasklist_guid = await self._ensure_tasklist_guid()
        data = await self._raw_request(
            "GET",
            f"/open-apis/task/v2/tasklists/{tasklist_guid}/sections",
        )
        if data.get("code") != 0:
            return [{"error": f"{data.get('code')}: {data.get('msg')}"}]
        items = data.get("data", {}).get("items", [])
        return [
            {"section_guid": s.get("guid", ""), "name": s.get("name", "")}
            for s in items
        ]

    async def task_snapshot(self) -> str:
        """Get a categorized snapshot of all open tasks.

        Returns formatted text with tasks grouped by: overdue, due_soon (within
        2 hours), and open (no due or future due).
        """
        tasks = await self.list_tasks(completed=False)
        if not tasks or (len(tasks) == 1 and "error" in tasks[0]):
            return "No open tasks."

        now = time.time()
        two_hours = now + 2 * 3600

        overdue: list[dict] = []
        due_soon: list[dict] = []
        open_tasks: list[dict] = []

        for t in tasks:
            # Fetch full details to get due date
            detail = await self.get_task(t["task_id"])
            due_ts = detail.get("due", "")
            summary = detail.get("summary", t.get("summary", ""))

            entry = {"task_id": t["task_id"], "summary": summary, "due": due_ts}

            if due_ts:
                try:
                    ts = float(due_ts)
                    if ts < now:
                        overdue.append(entry)
                    elif ts < two_hours:
                        due_soon.append(entry)
                    else:
                        open_tasks.append(entry)
                except (ValueError, TypeError):
                    open_tasks.append(entry)
            else:
                open_tasks.append(entry)

        lines: list[str] = []

        if overdue:
            lines.append(f"**Overdue ({len(overdue)})**")
            for t in overdue:
                lines.append(f"  - {t['summary']} (id: {t['task_id']})")

        if due_soon:
            lines.append(f"**Due Soon ({len(due_soon)})**")
            for t in due_soon:
                lines.append(f"  - {t['summary']} (id: {t['task_id']})")

        if open_tasks:
            lines.append(f"**Open ({len(open_tasks)})**")
            for t in open_tasks:
                lines.append(f"  - {t['summary']} (id: {t['task_id']})")

        if not lines:
            return "No open tasks."
        return "\n".join(lines)

    # ── Permission ─────────────────────────────────────────────

    async def set_public_sharing(
        self,
        doc_token: str,
        link_share_entity: str = "tenant_readable",
        doc_type: str = "docx",
    ) -> dict:
        """Set public sharing level for a document."""
        token = await self._get_tenant_token()
        url = (
            f"{self.domain}/open-apis/drive/v1/permissions/{doc_token}"
            f"/public?type={doc_type}"
        )
        body = {"link_share_entity": link_share_entity}
        try:
            async with httpx.AsyncClient() as http:
                resp = await http.patch(
                    url, headers={"Authorization": f"Bearer {token}"},
                    json=body, timeout=30,
                )
                data = resp.json()
                if data.get("code") != 0:
                    return {"error": f"{data.get('code')}: {data.get('msg')}"}
                return {"ok": True, "link_share_entity": link_share_entity}
        except Exception as e:
            return {"error": str(e)}
