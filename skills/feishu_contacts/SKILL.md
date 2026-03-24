---
name: feishu-contacts
description: 联系人管理与解析（contacts/联系人/通讯录）。用于查找用户 open_id（谁是XX/XX的open_id）、管理联系人别名（记住XX是XX）、跨 skill 共享联系人信息。自动从 API 响应中学习联系人映射。
---

# Feishu Contacts

跨 Skill 共享的联系人库。自动从 cal/task/perm 等 API 响应中学习用户信息。

## Actions

调用方式：`feishu_contacts(action, params)`

- **resolve** — 解析名称为 open_id。params: `{name}`。从已知联系人中匹配。
- **learn** — 手动记录联系人。params: `{open_id, name, alias?}`。
- **list** — 列出所有已知联系人。params: `{}`。
- **search** — 搜索联系人。params: `{query}`。模糊匹配名称和别名。

## Behavior Notes

- 联系人数据持久化到 data/contacts.json
- 从 cal attendees、task assignees、perm collaborators 的 API 响应中自动学习
- resolve 支持模糊匹配（拼音首字母、部分名称）
- 这是底层 library，其他 skill 通过 Python import 直接调用，不走 tool dispatch
