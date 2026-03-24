---
name: feishu-sheet
description: 飞书电子表格管理。用于创建表格、获取元数据、列出工作表、读写单元格区域。当用户提到电子表格、单元格、行列操作时触发。注意：多维表格用 feishu-bitable，电子表格是基于单元格的传统表格。
---

# Feishu Sheet

读写飞书电子表格（电子表格）。支持创建、元数据查询、工作表列表、单元格区域读写。

## Cross-skill boundary

Sheet ≠ Bitable。**Sheet** 是基于单元格的电子表格（电子表格），适合报表、数据导入导出、自由格式布局。**Bitable** 是有类型字段和视图的数据库（多维表格），适合结构化记录管理、过滤、聚合。两者 API 完全不同，不可混用。

## Key concepts

- **`spreadsheet_token`** — 从 URL 中提取，`/sheets/` 后的部分。例如 `https://xxx.feishu.cn/sheets/AbCdEfG123` → `AbCdEfG123`
- **`sheet_id`** — 工作表 tab 的 ID，非显示名称。**必须先调用 `info` 获取**，不要猜测或使用 tab 名。
- **Range format** — `"A1:B5"`（矩形区域）、`"A:C"`（整列）、`"1:3"`（整行）。未指定时默认 `"A1:Z100"`。
- **Values** — 二维数组，行 × 列。例如 `[["header1","header2"],["val1","val2"]]`。支持字符串、数字、布尔、null。

## Actions

调用方式：`feishu_sheet(action, params)`

- **`create`** — 创建电子表格。params: `{title, folder_token?}`。创建后自动通过 feishu_perm 将请求用户加为 full_access 协作者（非阻塞，失败不影响主流程）。
- **`info`** — 获取电子表格元数据（标题、工作表列表及各 sheet_id）。**不知道 sheet_id 时必须先调此接口。** params: `{spreadsheet_token}`。
- **`read_range`** — 读取单元格区域，返回二维数组。params: `{spreadsheet_token, sheet_id, range_str?}`。range_str 默认 `"A1:Z100"`。
- **`write_range`** — **覆写**指定区域（PUT 语义，非追加）。整个 range_str 区域会被替换。params: `{spreadsheet_token, sheet_id, range_str, values}`。

## Coded behaviors

以下行为由代码保证：

- **Auto-share on create** — `create` 调用结束后自动调用 `ensure_user_access`，失败不抛出异常，不阻塞返回。
- **write_range 是覆写** — 使用飞书 PUT 接口，无追加/插入模式。如需追加，先 `read_range` 确认末行位置，再计算目标 range。
- API 版本：数据读写用 Sheets API v2，元数据用 v3。
- Bot 需要 `sheets:spreadsheet` 权限 scope。

## Scope boundaries — 不适合本 skill 的场景

- 批量处理 100+ 行并附带 LLM 逐行逻辑（性能瓶颈，改写专用 Python 脚本）
- 复杂条件查询（Sheet 无服务端过滤，不像 Bitable）
- 跨 sheet 联表或聚合转换
- 大批量数据 ETL — 直接调用 Sheets API 编写专用脚本

## Params examples

```python
# 获取元数据（取得 sheet_id 列表）
feishu_sheet("info", {"spreadsheet_token": "AbCdEfG123"})

# 读取区域
feishu_sheet("read_range", {
    "spreadsheet_token": "AbCdEfG123",
    "sheet_id": "abc123",
    "range_str": "A1:D10"
})

# 覆写区域
feishu_sheet("write_range", {
    "spreadsheet_token": "AbCdEfG123",
    "sheet_id": "abc123",
    "range_str": "A1:B3",
    "values": [["Name", "Score"], ["Alice", 95], ["Bob", 88]]
})

# 创建电子表格（自动授权请求用户）
feishu_sheet("create", {"title": "月度报表", "folder_token": "FolderTokenXxx"})
```
