# 原始 TODO 的实现与迁移说明

代码基准：2026-10-13 `main`。**功能已在仓库中实现**不等于已生产部署，是否部署需由维护者核对。

## 六项原始需求

| 原始需求 | 当前代码 |
| --- | --- |
| 任务树与前置任务 | 已实现，树和依赖分别防环；建议开始日期仅提示 |
| 工作截图 | 已实现受保护的 PNG/JPEG/WebP 附件，单次最多 5 张、单张最多 10 MiB |
| 一主责、多参与成员 | 已实现 `owner_id` + `task_participants` |
| 同时处于多个专业组 | 已实现 `groups` + `member_groups`，默认视觉/电控/机械/硬件/宣传/运营 |
| 任务按造车组别归档 | 已实现 `projects` + `tasks.project_id`；成员可同时参与多个造车组别 |
| GitHub Issue/Discussion | 已实现 **外部链接入口**，无自动双向同步、无需将业务数据传给 GitHub |

## 迁移概览

`modules/task_migrations.py` 通过 `schema_migrations` 记录升级版本并用 SQLite 事务执行：

- v1：创建任务扩展关系（树、依赖、参与者、修订、截图等），从旧成员/任务字段迁移目录引用。
- v2：把历史中使用分号拼接的专业组拆分为多组关联，**仅一次**预置六个专业组。
- v3：新增 `member_projects`，**仅一次**根据历史任务的负责人/参与者与 `project_id` 推断成员参与的造车组别；默认预置步兵/哨兵/重装/飞镖，并保留旧细分项目。

`task_groups` 仍为历史数据/API 兼容存在，不再决定任务造车组别归属。`members.group_name` 与 `tasks.project` 等旧字段不是新关联的权威来源。

成员、专业组、造车组别的 `deleted_at` 表示软删除，管理员可恢复；**任务逻辑删除目前没有恢复入口**。

## 验证与发布约束

    python -m unittest discover -s tests -v
    python deploy/rehearse_collaboration_recovery.py

合成数据演练验证迁移可重复运行和隔离恢复，不读取生产数据库。升级生产时务必停写，对 `data.db`、资金、发票、任务截图和 `secret.key` 制作**同一恢复点**；单独执行当前 `deploy/backup.sh` 不充分。详见[发布检查与数据恢复](发布检查与数据恢复.md)。

[原始需求与当前待办](../../TODO.md) · [系统架构](系统架构与技术说明.md)
