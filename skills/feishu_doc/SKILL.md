---
name: feishu-doc
description: 飞书文档（结构化沟通）。用于创建文档（写个文档/建个文档）、整理讨论结果、读取飞书文档链接、保存内容到文档、查看/回复文档评论。输出超过 1000 字且涉及多维度时自动触发（用文档而非聊天刷屏），方案讨论优先用文档（支持评论批注）。
---

# Feishu Documents

不只是写文档——是结构化沟通工具。持久、可引用、可评论。

**自动触发规则**：
1. 超过 1000 字以上、涉及 2 个以上问题或维度的反馈 → 使用飞书文档（方便对具体细节讨论和交流）
2. 项目方案需要讨论确认 → 优先用文档（用户可在文档内评论批注）

**与 feishu-wiki 的边界**（待实现）：doc 是沟通产物（方案、评审、会议纪要）——有时效性，写完讨论完可能不再更新。wiki 是知识沉淀（规范、指南、FAQ）——持续维护，供反复查阅。

## 写入流程

写文档是两步操作：**先撰写 Markdown，再调用工具写入**。

1. 先在回复中完整撰写 Markdown 内容（用户可以预览确认）
2. 再调用 `create` + `append`（或直接 `append` 到已有文档）写入

系统会自动将 Markdown 转换为飞书文档原生格式（标题、列表、代码块、表格等）。

## 支持的 Markdown 语法

以下语法会被正确渲染为飞书文档原生格式：

| 语法 | 写法 | 渲染效果 |
|------|------|----------|
| 标题 | `# H1` ~ `###### H6` | 飞书标题 1-6 |
| 粗体 | `**文本**` | **粗体** |
| 斜体 | `*文本*` | *斜体* |
| 删除线 | `~~文本~~` | ~~删除线~~ |
| 行内代码 | `` `代码` `` | 行内代码 |
| 链接 | `[文本](https://url)` | 可点击链接 |
| 代码块 | ` ```python\n代码\n``` ` | 带语法高亮的代码块 |
| 无序列表 | `- 项目` 或 `* 项目` | 飞书列表 |
| 有序列表 | `1. 项目` | 飞书编号列表 |
| 嵌套列表 | 2 空格缩进子项 | 层级列表 |
| 引用 | `> 引用文字` | ▎前缀引用（支持内联格式） |
| 表格 | 标准 Markdown 表格 | 飞书原生表格（最大 9 列 × 100 行） |
| 分割线 | `---` | 飞书分割线 |

**不支持**（会被降级或忽略）：
- `<font color>` → 提取纯文本（飞书文档不支持颜色）
- `<text_tag>` → 提取纯文本
- `<at id=...>` → 移除
- `{{card:header=...}}` → 移除（仅聊天卡片生效）
- `:DONE:` `:OK:` 等飞书表情 → 保留原文

## CLI 调用方式

```bash
cd ~/Agent\ Space/agentic-feishu && python3 tools/builtin/skill_doc.py <action> --params '<json>'
```

示例：
```bash
python3 tools/builtin/skill_doc.py read --params '{"document_id": "xxx"}'
```

## Actions

调用方式：`python3 tools/builtin/skill_doc.py <action> --params '<json>'`

- **create** — 创建文档。params: `{title, folder_token?}`。创建后必须将请求用户加为 full_access 协作者。返回完整链接 `https://feishu.cn/docx/{document_id}`。
- **read** — 读取文档内容。params: `{document_id}`。支持传入完整 URL 或裸 document_id。
- **append** — 追加 Markdown 内容到文档末尾。params: `{document_id, content}`。同主题文档已存在时不要新建，直接 append。content 使用上述支持的 Markdown 语法。
- **update** — 全量替换文档内容（破坏性操作）。params: `{document_id, content}`。局部更新优先用 append 或 replace_section。
- **replace_section** — 替换指定标题下的章节内容。params: `{document_id, heading_title, new_content}`。删除从匹配标题到下一个同级标题之间的内容，插入 new_content。
- **search** — 按关键词搜索文档。params: `{query, count?=10}`。用短关键词，不要整句话。
- **list_comments** — 列出文档所有评论。params: `{document_id}`。
- **reply_comment** — 回复某条评论。params: `{document_id, comment_id, content}`。
- **transfer_owner** — 转移文档所有权（不可逆，执行前必须与用户确认）。params: `{document_id, new_owner_id}`。
- **send_message** — 向飞书群组/用户发送消息。params: `{chat_id, text}`。仅在用户明确要求时调用，不主动发送到其他群聊。

## Behavior Notes

以下规则由代码保证，供参考：

- 文档由 Bot 创建，所有权属于 App。需要时用 transfer_owner 转交给用户（推荐在用户请求创建文档时执行）。
- `search` 使用客户端关键词过滤，基于 Bot 可访问的共享文件夹内容（不调用需要用户 OAuth 的服务端搜索 API）。
- `replace_section` 精确匹配标题文本，删除到下一个同级标题前，再插入新内容。
- `send_message` 仅在用户明确指示时触发，不做主动推送。
