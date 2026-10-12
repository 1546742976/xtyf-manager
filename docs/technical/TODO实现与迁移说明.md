# TODO 实现与迁移说明

## GitHub 协作入口

工作台的“GitHub 协作”页面只生成并展示仓库 Discussions 和新建 Issue 的外链。仓库由 `GITHUB_REPOSITORY` 配置，格式必须是 `owner/repository`；默认值是 `SHM-white/xtyf-manager`。配置为空或格式无效时，页面会显示配置指引，不生成链接。

页面不调用 GitHub API，也不保存讨论副本，更不会传输工作台中的任务、成员、图片或其他元数据。私有仓库需要用户使用已获授权的 GitHub 账号登录，并已获得仓库或组织访问权。

Issue 模板位于 `.github/ISSUE_TEMPLATE/`，Discussion 想法模板位于 `.github/DISCUSSION_TEMPLATE/`。模板要求使用脱敏示例，禁止提交密码、令牌、个人联系方式、未公开文件或内部工作数据。仓库仍为私有仓库，Issues 和 Discussions 已启用；这些模板会在包含它们的提交进入默认分支后生效。

## 协作任务实现状态

任务协作功能已经实现，并完成浏览器流程验证；仍待最终源码审查和生产部署。本节描述待部署版本的运行行为，不能据此推断线上实例已经升级。

### 迁移与数据模型

应用初始化会调用版本化迁移。迁移以一个 SQLite 事务创建或补齐以下结构，并在 `schema_migrations` 中记录版本；成功后重复运行不会重复回填关系，也不会重新加入后来被人工移除的成员组关系。

| 结构 | 用途 |
|---|---|
| `groups`、`projects` | 规范化的专业组与项目目录；一个任务只关联一个项目。 |
| `member_groups`、`task_groups` | 成员和任务的多组关系。 |
| `task_participants` | 一个任务的多个参与者；`owner_id` 仍是单一总负责人。 |
| `task_dependencies` | 前置依赖，独立于任务树。 |
| `tasks.parent_id` | 任务树父节点。 |
| `tasks.version`、`task_revisions` | 乐观并发版本和逐字段 before/after 修订记录。 |
| `tasks.deleted_at` | 逻辑删除标记。 |
| `task_attachments` | 私有截图的元数据，实体文件不在静态目录。 |

旧 `members.group_name`、`tasks.group_name` 和 `tasks.project` 保持兼容。迁移只去除首尾空格，不会按逗号、分号或其他字符拆分和猜测旧名称。历史关联允许保留停用成员；新的负责人、参与者、组别和项目分配只接受有效的活动目录项。

### 任务 API、权限与并发

- `POST /api/task` 创建任务；`POST /api/task/<id>` 更新任务；请求与响应携带 `version`，陈旧版本返回冲突而不会覆盖新数据。
- `admin` 与 `manager` 可创建、编辑和逻辑删除任务。任务负责人或参与者可编辑自己参与的任务的全部普通设置，包括日期；**只有 `admin`** 可设置或修改 `parent_id`。
- 父任务树与前置依赖图分别验证无环。前置依赖仅返回未完成风险和建议开始日期，永不自动改写开始/截止日期。
- `POST /api/task/<id>/delete` 是逻辑删除：存在未删除子任务或仍被其他任务依赖时返回冲突；成功删除后保留任务日志、字段修订和附件元数据。当前没有用户可用的恢复端点。
- `GET /api/task/<id>/revisions` 返回修订；管理员通过 `POST /api/task/<id>/revisions/<revision_id>/revert` 按修订撤销。若该修订涉及的字段已经在之后被触碰，撤销返回冲突；撤销不会擦除备注或截图。

### 截图附件

附件 API 位于 `/api/task/<id>/attachments`。读取、上传、下载和删除都经过登录与任务权限检查，文件不走公开 `static/` 路由。

- 每次请求最多上传 5 张，每张不超过 10 MiB，只接受经内容校验的 PNG、JPEG 或 WebP。
- 管理角色、负责人和参与者可上传；上传者本人或管理角色可删除。
- 上传先暂存并写入数据库元数据；任一步失败会回滚数据库并清理暂存文件。删除同样协调文件与元数据，避免孤立文件或孤立记录。

### 页面和 CSV 兼容

任务工作台提供组别/项目/参与者筛选、祖先上下文任务树、前置依赖警告、修订历史与截图留痕。GitHub 页面仅显示 Discussions/Issue 外部链接，不调用 GitHub API，也不会把任务、成员、截图或附件发送到 GitHub。

成员导入和导出 CSV 的**列**使用逗号分隔；单个“组别”字段内的多个组名使用分号分隔。任务与成员导出会保留既有列，并为以 `=`, `+`, `-` 或 `@` 开头的单元格添加保护前缀，避免表格软件执行公式。

## 升级、备份与恢复

现有 `deploy/backup.sh` 每日对进度/资金库使用 SQLite 备份，并按周打包发票。它**不包含** `data/task_attachments/`，也不把 `secret.key` 放入同一恢复点，因此不能单独作为协作版本的完整备份方案。在升级或恢复协作数据前，使用以下人工一致性流程：

1. 暂停所有写入，推荐执行 `systemctl stop rm-progress.service`。不能在应用写入时混合复制数据库和文件。
2. 在同一恢复点保存 `data.db` 与其 `data.db-wal` / `data.db-shm`、`data/funds/`、`data/invoices/`、`data/task_attachments/` 和 `secret.key`。服务停止后可以完整复制；也可在写入暂停期间对每个 SQLite 库运行在线备份，再复制同一时点的文件。
3. 先恢复到隔离目录。对恢复的每个数据库运行 `PRAGMA integrity_check`，并核对成员、任务、日志、关系、附件元数据及附件文件的计数和摘要。
4. 只有所有数据库、附件、资金、发票和密钥来自同一恢复点且核对通过后，才允许恢复服务或切换数据目录。

### 隔离演练

仓库提供不接触真实数据库、真实附件、发票或 `secret.key` 的演练：

```powershell
uv run deploy/rehearse_collaboration_recovery.py
```

该命令在临时目录创建包含一名活动成员、一名停用成员、任务、日志、合成附件和临时密钥的旧结构；它迁移两次，验证旧内容摘要与移除关系不被重新写回，验证中途失败回滚 DDL/列/关系，使用 `sqlite3.Connection.backup()` 备份，修改源数据库和附件后把两者恢复到独立目录，并运行 `PRAGMA integrity_check`。命令完成后删除临时夹，并把无敏感信息的结果写入 `.omo/evidence/todo-collaboration/recovery/rehearsal-report.json`。
