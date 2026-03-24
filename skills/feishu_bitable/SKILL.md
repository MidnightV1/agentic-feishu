---
description: Manage Feishu Bitable / multidimensional tables (多维表格) — create apps, list tables, query/add/update/delete records with filtering. Use when the user mentions bitable, multidimensional table (多维表格), spreadsheet database (数据表), structured data, or wants to query/update table records in Feishu. DO NOT TRIGGER for regular spreadsheets (电子表格/单元格) — use feishu-sheet for those. Bitable is a database with typed fields and views; Sheet is a cell-based spreadsheet.
---

# Feishu Bitable

飞书多维表格（Base）的 CRUD 操作。查询记录、添加行、更新字段、管理表格。

**与 feishu-sheet 的边界**：Bitable 是有类型字段和视图的数据库（多维表格），feishu-sheet 是基于单元格的电子表格。数据库式操作用 Bitable，表格/报表式操作用 Sheet。

## URL Parsing

从飞书 URL 提取参数：

```
https://xxx.feishu.cn/base/AbCdEfG123?table=tblXXX&view=vewYYY
```

- `app_token` = `AbCdEfG123`（路径 `/base/` 后的 segment）
- `table_id` = `tblXXX`（query param `table=`，以 `tbl` 开头）
- `view` 参数可忽略，query 时可选传

## Actions

调用方式：`feishu_bitable(action, params)`

- **create** — 创建 Bitable 应用。params: `{name, folder_token?}`。创建后自动调用 feishu_perm 将请求用户加为 full_access 协作者（失败不阻断）。
- **list_tables** — 列出应用中的所有表格。params: `{app_token}`。
- **query** — 查询记录。params: `{app_token, table_id, filter_expr?, page_size?=20}`。`filter_expr` 使用飞书公式语法（如 `CurrentValue.[Status]="Done"`），字段名**区分大小写**，page_size 默认 20，最大 500。
- **add_record** — 添加记录。params: `{app_token, table_id, fields}`。`fields` 为字段名到值的字典，字段名必须与表格定义**完全一致**（区分大小写）。
- **update_record** — 更新记录。params: `{app_token, table_id, record_id, fields}`。只传需要修改的字段。
- **delete_record** — 删除记录（不可逆）。params: `{app_token, table_id, record_id}`。

## Field Value Formats

`fields` 字典中常见字段值格式：

| 字段类型 | 格式示例 |
|---------|---------|
| 文本 | `"field_name": "value"` |
| 数字 | `"field_name": 123` |
| 单选 | `"field_name": "option_name"` |
| 多选 | `"field_name": ["opt1", "opt2"]` |
| 日期 | `"field_name": 1709539200000`（毫秒时间戳） |
| 复选框 | `"field_name": true` |
| 人员 | `"field_name": [{"id": "ou_xxx"}]` |
| 超链接 | `"field_name": {"link": "https://...", "text": "label"}` |

## Coded Behaviors

以下规则由代码保证：

- **Auto-share on create**：`create` 后自动调用 `ensure_user_access`（feishu_perm 集成），赋予请求用户 full_access，失败不阻断主流程。
- **Query defaults**：`page_size` 默认 20，最大 500。
- **Field case-sensitive**：`filter_expr` 中的字段名和 `fields` 字典的 key 均区分大小写，必须与表格列名完全一致。
- Bot 需要 `bitable:app` 权限 scope。

## Params Examples

```python
feishu_bitable("list_tables", {"app_token": "AbCdEfG123"})

feishu_bitable("query", {
    "app_token": "AbCdEfG123",
    "table_id": "tblXXX",
    "filter_expr": 'CurrentValue.[Status]="Done"',
    "page_size": 50
})

feishu_bitable("add_record", {
    "app_token": "AbCdEfG123",
    "table_id": "tblXXX",
    "fields": {"Name": "Test", "Status": "Todo"}
})
```
