---
description: Manage Feishu document permissions (权限管理) — add/remove collaborators, set public sharing, check access levels. Use when the user mentions sharing (分享/共享), permissions (权限), collaborators (协作者), access control (访问控制), visibility (公开/私有), link sharing (链接分享), or wants to manage who can view/edit a document. Also trigger when user says "把文档分享给XX"、"谁能看这个文档"、"设成公开". DO NOT TRIGGER for creating or editing document content — use feishu-doc for that.
---

# Feishu Permission Manager

管理文档/文件权限——添加协作者、设置公开链接分享、查看访问级别。

**边界**：feishu-perm 管理"谁能访问"，feishu-doc 管理文档内容本身。转移所有权走 feishu-doc 的 `transfer_owner`。

---

## Actions

调用方式：`feishu_perm(action, params)`

| Action | 说明 | 必填参数 | 可选参数（含默认值） |
|--------|------|----------|----------------------|
| `list` | 列出文档所有协作者及权限级别 | `doc_token` | `doc_type="docx"` |
| `get_sharing` | 获取当前公开分享设置 | `doc_token` | `doc_type="docx"` |
| `add` | 添加协作者 | `doc_token`, `member_id` | `perm="full_access"`, `member_type="openid"`, `doc_type="docx"` |
| `remove` | 移除协作者（不可逆） | `doc_token`, `member_id` | `member_type="openid"`, `doc_type="docx"` |
| `set_sharing` | 设置链接分享模式 | `doc_token` | `link_share_entity="tenant_readable"`, `doc_type="docx"` |

---

## Permission Levels (`perm`)

| 值 | 说明 |
|----|------|
| `view` | 只读 |
| `edit` | 可编辑 |
| `comment` | 可评论 |
| `full_access` | 完全控制（可管理权限） |

---

## Doc Types (`doc_type`)

有效值：`docx`、`sheet`、`bitable`、`folder`、`file`、`mindnote`、`slides`、`wiki`

`doc_type` 必须与文档实际类型匹配，否则 API 返回错误。

---

## Member Types (`member_type`)

| 值 | 说明 |
|----|------|
| `openid`（默认） | 用户 open_id |
| `userid` | 用户 user_id |
| `chatid` | 群组 ID |
| `departmentid` | 部门 ID |

`member_type` 决定 `member_id` 的解释方式，必须与实际 ID 类型一致。

---

## Link Sharing Options (`link_share_entity`)

| 值 | 说明 |
|----|------|
| `tenant_readable` | 组织内可查看（默认） |
| `tenant_editable` | 组织内可编辑 |
| `anyone_readable` | 链接可查看 |
| `anyone_editable` | 链接可编辑 |
| `closed` | 仅协作者可访问 |

---

## Coded Behaviors

- **枚举校验**：`perm`、`member_type`、`doc_type`、`link_share_entity` 传入无效值时抛出 `ValueError`，message 说明有效值列表。
- `member_type` 影响 `member_id` 的解释方式，两者必须一致。
- `doc_type` 必须与文档实际类型匹配。
- Bot 需要 `drive:permission` 和 `drive:permission:readonly` scope。

---

## Examples

```python
feishu_perm("list", {"doc_token": "xxx", "doc_type": "docx"})

feishu_perm("add", {"doc_token": "xxx", "member_id": "ou_yyy", "perm": "edit", "doc_type": "docx"})

feishu_perm("remove", {"doc_token": "xxx", "member_id": "ou_yyy"})

feishu_perm("set_sharing", {"doc_token": "xxx", "link_share_entity": "anyone_readable", "doc_type": "sheet"})
```
