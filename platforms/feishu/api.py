# -*- coding: utf-8 -*-
"""Feishu API client — thin async wrapper over lark-oapi.

Provides document, task, calendar, and message operations.
All methods are async and return dicts/lists.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

log = logging.getLogger("agentic.feishu.api")


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

    async def append_document(self, document_id: str, content: str) -> dict:
        """Append markdown blocks to a document — simplified version."""
        # Full block-level append would use docx.v1.document_block
        # For now, create text blocks from markdown
        client = self._ensure_client()
        from lark_oapi.api.docx.v1 import (
            CreateDocumentBlockChildrenRequest,
            CreateDocumentBlockChildrenRequestBody,
        )

        blocks = [
            {
                "block_type": 2,
                "text": {
                    "elements": [{"text_run": {"content": content}}],
                    "style": {},
                },
            }
        ]

        req = (
            CreateDocumentBlockChildrenRequest.builder()
            .document_id(document_id)
            .block_id(document_id)  # root block
            .request_body(
                CreateDocumentBlockChildrenRequestBody.builder()
                .children(blocks)
                .build()
            )
            .build()
        )
        resp = await client.docx.v1.document_block_children.acreate(req)

        if not resp.success():
            return {"error": f"{resp.code}: {resp.msg}"}
        return {"ok": True, "blocks_added": len(blocks)}

    async def search_documents(self, query: str, count: int = 10) -> list:
        client = self._ensure_client()
        from lark_oapi.api.suite.v1 import SearchObjectRequest

        req = (
            SearchObjectRequest.builder()
            .query(query)
            .count(count)
            .build()
        )

        try:
            resp = await client.suite.v1.search_object.asearch(req)
            if not resp.success():
                return [{"error": f"{resp.code}: {resp.msg}"}]
            items = resp.data.items or []
            return [
                {
                    "title": item.title,
                    "url": item.url,
                    "type": item.type,
                }
                for item in items
            ]
        except Exception as e:
            return [{"error": str(e)}]

    # ── Tasks ─────────────────────────────────────────────────────

    async def create_task(
        self, title: str, due_date: str = "", description: str = ""
    ) -> dict:
        client = self._ensure_client()
        from lark_oapi.api.task.v2 import CreateTaskRequest, CreateTaskRequestBody

        body_builder = CreateTaskRequestBody.builder().summary(title)
        if description:
            body_builder = body_builder.description(description)
        if due_date:
            body_builder = body_builder.due({"timestamp": due_date, "is_all_day": True})

        req = CreateTaskRequest.builder().request_body(body_builder.build()).build()
        resp = await client.task.v2.task.acreate(req)

        if not resp.success():
            return {"error": f"{resp.code}: {resp.msg}"}
        return {
            "task_id": resp.data.task.guid,
            "summary": resp.data.task.summary,
        }

    async def list_tasks(self, completed: bool = False) -> list:
        client = self._ensure_client()
        from lark_oapi.api.task.v2 import ListTaskRequest

        req = ListTaskRequest.builder().page_size(50).build()
        resp = await client.task.v2.task.alist(req)

        if not resp.success():
            return [{"error": f"{resp.code}: {resp.msg}"}]
        tasks = resp.data.items or []
        result = []
        for t in tasks:
            is_done = bool(getattr(t, "completed_at", None))
            if is_done and not completed:
                continue
            result.append({
                "task_id": t.guid,
                "summary": t.summary,
                "completed": is_done,
            })
        return result

    async def complete_task(self, task_id: str) -> dict:
        client = self._ensure_client()
        from lark_oapi.api.task.v2 import CompleteTaskRequest

        req = CompleteTaskRequest.builder().task_guid(task_id).build()
        resp = await client.task.v2.task.acomplete(req)

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

    async def list_events(self, days: int = 7) -> list:
        client = self._ensure_client()
        from lark_oapi.api.calendar.v4 import ListCalendarEventRequest

        now = int(time.time())
        end = now + days * 86400

        req = (
            ListCalendarEventRequest.builder()
            .calendar_id("primary")
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
    ) -> dict:
        client = self._ensure_client()
        from lark_oapi.api.calendar.v4 import (
            CreateCalendarEventRequest,
            CreateCalendarEventRequestBody,
        )

        body = {
            "summary": summary,
            "start_time": {"timestamp": start_time},
            "end_time": {"timestamp": end_time},
        }
        if description:
            body["description"] = description

        req = (
            CreateCalendarEventRequest.builder()
            .calendar_id("primary")
            .request_body(body)
            .build()
        )

        try:
            resp = await client.calendar.v4.calendar_event.acreate(req)
            if not resp.success():
                return {"error": f"{resp.code}: {resp.msg}"}
            return {
                "event_id": resp.data.event.event_id,
                "summary": summary,
            }
        except Exception as e:
            return {"error": str(e)}

    # ── Media download ─────────────────────────────────────────────

    async def _get_tenant_token(self) -> str:
        """Get or refresh tenant access token."""
        now = time.time()
        if self._token_cache["token"] and self._token_cache["expires"] > now:
            return self._token_cache["token"]

        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{self.domain}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            )
            data = resp.json()
            token = data.get("tenant_access_token", "")
            expire = data.get("expire", 7200)
            self._token_cache = {"token": token, "expires": now + expire - 300}
            return token

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

    async def create_drive_folder(self, name: str, parent_token: str = "") -> dict:
        """Create a folder in Drive."""
        token = await self._get_tenant_token()
        url = f"{self.domain}/open-apis/drive/v1/files/create_folder"
        body: dict[str, Any] = {"name": name}
        if parent_token:
            body["folder_token"] = parent_token
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

    # ── Permission ─────────────────────────────────────────────────

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
