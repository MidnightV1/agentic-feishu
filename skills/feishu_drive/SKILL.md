---
description: Manage Feishu Drive / cloud storage (云盘/云空间) — list files/folders, create folders, move/delete files, search. Use when the user mentions cloud drive (云盘/云空间), file management (文件管理), folders (文件夹), file organization (整理文件), finding files (找文件/找个文档). DO NOT TRIGGER for reading/writing document content — use feishu-doc for that. Drive manages the file tree; doc manages content inside a document.
---

# Feishu Drive

浏览、组织和管理飞书云盘中的文件和文件夹。

**边界**：Drive 管理文件树（文件的位置、组织结构），feishu-doc 管理文档内容（读写文档正文）。

## File Types

| file_type | 说明 |
|-----------|------|
| `docx` | 文档 |
| `sheet` | 电子表格 |
| `bitable` | 多维表格 |
| `folder` | 文件夹 |
| `file` | 上传文件 |
| `mindnote` | 思维笔记 |
| `slides` | 幻灯片 |

## Actions

调用方式：`feishu_drive(action, params)`

- **list** — 列出文件/文件夹。params: `{folder_token?, page_size?=20}`。不传 `folder_token` 则列根目录。
- **search** — 按关键词搜索文件。params: `{query, count?=10}`。关键词匹配文件名。
- **create_folder** — 创建文件夹。params: `{name, parent_token, force?=false}`。`parent_token` 必填。自动去重（见 Coded Behaviors）；自动授予创建者 full_access。
- **move** — 移动文件/文件夹。params: `{file_token, target_folder_token}`。两个参数均必填。
- **delete** — 删除文件/文件夹（移入回收站，30 天内可恢复，非永久删除）。params: `{file_token, file_type, confirmed?=false}`。两段式确认（见 Coded Behaviors）。`file_type` 取值见上表。

## Coded Behaviors

**Auto-dedup on create_folder**：创建文件夹前自动检查 `parent_token` 下是否已存在同名文件夹。若存在，返回重复信息（含已有文件夹的 token）而不创建，除非传 `force=true`。未提供 `parent_token` 时跳过去重检查。

**Auto-share on create_folder**：创建成功后自动调用 `ensure_user_access`，授予创建者 `full_access`。此步骤失败不阻塞主流程。

**Confirmation-gated delete（两段式）**：第一次调用返回确认提示，不执行删除；第二次传 `confirmed=true` 才真正执行。目的是防误删。

**Delete = trash**：删除操作将文件移入回收站，30 天内可从回收站恢复，期满后不可逆。

## Params Examples

```
feishu_drive("list", {"folder_token": "fldcnXXX", "page_size": 50})
feishu_drive("list", {})                                                          # 列根目录
feishu_drive("create_folder", {"name": "项目文档", "parent_token": "fldcnXXX"})
feishu_drive("create_folder", {"name": "项目文档", "parent_token": "fldcnXXX", "force": true})  # 跳过去重
feishu_drive("delete", {"file_token": "xxx", "file_type": "docx"})               # 第一次：返回确认提示
feishu_drive("delete", {"file_token": "xxx", "file_type": "docx", "confirmed": true})  # 第二次：执行删除
feishu_drive("search", {"query": "周报", "count": 20})
feishu_drive("move", {"file_token": "xxx", "target_folder_token": "fldcnYYY"})
```
