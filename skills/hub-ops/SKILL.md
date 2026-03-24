---
name: hub-ops
description: Hub 服务管理与定时任务操作。查看/启用/禁用定时任务，查看服务状态。
---

# Hub Operations

Hub 服务管理和 Scheduler 定时任务的运行时控制。

## 调用方式

此 skill 的操作通过进程内调用完成（Scheduler 实例在内存中），不支持独立 CLI。
LLM 通过 bash tool 调用时需使用进程内路由。

## Actions

- **job_list** — 列出所有定时任务（含已禁用）。无参数。
- **job_enable** — 启用定时任务。params: `{name: str}`
- **job_disable** — 禁用定时任务。params: `{name: str}`
- **scheduler_reload** — 刷新 Scheduler 状态。无参数。
- **service_status** — 查看服务运行状态和错误摘要。无参数。

## Notes

- Job 的创建/删除需要代码变更（handler 是 async 函数），运行时只能 enable/disable。
- scheduler_reload 保留 handler 但更新 schedule/enabled 状态。
