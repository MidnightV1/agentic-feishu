---
description: Manage Feishu calendar events (日历/日程) — create, list, update, delete events, invite attendees. Use when the user mentions meetings (会议/开会), schedule (日程/排期), calendar (日历), events, appointments, blocking time (约时间), or inviting people. Calendar answers "什么时候在哪做什么" — time-anchored events you need to "be present" for.
---

# Feishu Calendar

Bot 拥有独立日历，创建的日程用户可见。涉及用户的事件必须将用户加为 attendee 才能同步到用户日历。

**与 feishu-task 的边界**：日历管理时间锚点（有开始/结束、需要"在场"的事件），任务管理承诺追踪（有状态流转、需要"做完"的事项）。重叠时：如"周三 14:00 面试候选人"建日历事件，如果还有准备工作（"面试前看简历"）额外建任务。

## Time Formats

所有时间参数均使用 Asia/Shanghai 时区。

| 格式 | 示例 | 说明 |
|------|------|------|
| ISO | `2026-03-03T10:00` | 标准格式，首选 |
| 日期+时间 | `2026-03-03 10:00` | 等同于 ISO |
| 仅时间 | `14:00` | 今天该时刻；若已过则顺延到明天 |
| tomorrow | `tomorrow 10:00` | 明天指定时刻 |
| 相对偏移 | `+2h` / `+30m` | 从当前时刻起偏移 |

## Actions

调用方式：`feishu_cal(action, params)`

- **create** — 创建日程。params: `{summary, start_time, end_time, description?, attendees?}`。必须将请求用户（消息上下文中的 open_id）加为参会人，确保日程出现在用户日历中。创建后必须告知用户（标题 + 时间）。`attendees` 接受：CSV 字符串、字符串列表、或字典列表（自动提取 open_id），代码自动归一化为字符串列表。

- **list** — 列出近期日程。params: `{days?=7}`。返回未来 N 天的事件列表。

- **update** — 更新日程。params: `{event_id, summary?, start_time?, end_time?, description?}`。只传需要修改的字段。**不支持通过 update 修改参会人**，必须用 add_attendees / remove_attendees。

- **delete** — 删除日程。params: `{event_id, confirmed?}`。**两阶段确认**：第一次调用（不传 `confirmed` 或 `confirmed=false`）返回确认提示，不执行删除；第二次调用传 `confirmed=true` 才真正删除。

- **freebusy** — 查询空闲/忙碌状态。params: `{start_time, end_time, user_ids?}`。`user_ids` 为逗号分隔的 open_id 字符串或列表；不传则查 Bot 自身日历。

- **list_attendees** — 列出日程参会人。params: `{event_id}`。返回参会人列表，含 `attendee_id`（记录 ID）、`user_id`（open_id）、`display_name`、`status`。

- **add_attendees** — 添加参会人到已有日程。params: `{event_id, attendee_ids}`。`attendee_ids` 为 open_id（逗号分隔字符串或列表）。

- **remove_attendees** — 移除日程参会人。params: `{event_id, attendee_ids}`。**CRITICAL**：`attendee_ids` 必须是 `list_attendees` 返回的 `attendee_id`（内部记录 ID），不是 open_id。必须先调 `list_attendees` 获取正确 ID。

## Coded Behaviors

以下由 Python 代码自动处理，无需在调用时特殊处理：

- **Attendee 归一化**：`attendees` / `attendee_ids` 传入字符串、字典、或混合列表均自动提取为 open_id 字符串列表；CSV 字符串自动拆分
- **user_ids CSV**：`freebusy` 的 `user_ids` 传逗号分隔字符串时自动拆分为列表
- **删除确认门控**：`delete` 未传 `confirmed=true` 时只返回提示文本，不触发 API 调用

## Params Examples

```python
# 创建日程，邀请两位参会人
feishu_cal("create", {"summary": "周会", "start_time": "2026-03-03T10:00", "end_time": "2026-03-03T11:00", "attendees": ["ou_xxx", "ou_yyy"]})

# 列出未来 3 天日程
feishu_cal("list", {"days": 3})

# 删除日程（第二阶段，已确认）
feishu_cal("delete", {"event_id": "xxx", "confirmed": true})

# 移除参会人（attendee_ids 来自 list_attendees 的 attendee_id 字段，非 open_id）
feishu_cal("remove_attendees", {"event_id": "xxx", "attendee_ids": "attendee_record_id_1,attendee_record_id_2"})
```

## Limitations

- `update` 不支持修改参会人，需单独调 `add_attendees` / `remove_attendees`
- `remove_attendees` 必须用 `attendee_id`（记录 ID），误传 open_id 会失败，必须先 `list_attendees`
- 联系人管理（contact add/list/sync）待实现，当前 attendees 直接传 open_id
