# 源码导读（按功能定位）

当前执行入口为仓库根目录的 `app.py`，不再使用旧“源代码注释版”副本。

| 路径 | 主要职责 |
| --- | --- |
| `app.py` | Flask 初始化、入口页、登录/退出、初始数据库、导出及基础成员接口 |
| `common.py` | `PROGRESS_DB`、请求级数据库连接、用户会话与权限装饰器 |
| `modules/task_api.py` | 任务 API 路由 |
| `modules/daily_projects.py` / `daily_schema.py` / `static/daily-projects.js` | 独立日常目标、组负责人、组别进度、迁移及工作台摘要 |
| `modules/email_api.py` / `mail_service.py` / `deploy/mail_worker.py` | 邮箱验证、08:00 个人早报、22:00 团队晚报、持久队列、SMTP 与模拟发送 |
| `modules/task_schedule.py` / `static/task-schedule.js` | 专业阶段判定、排期预览、快照冲突校验、原子保存及预览交互 |
| `modules/task_mutations.py` / `task_validation.py` | 任务新增、更新及输入合法性校验 |
| `modules/task_queries.py` / `task_types.py` | 看板数据组织与共享类型 |
| `modules/task_delete.py` / `task_revisions.py` | 逻辑删除、字段修订与撤销 |
| `modules/task_migrations.py` / `task_catalogs.py` | 版本化迁移、两套组别字典及成员关联 |
| `modules/member_profiles.py` | 个人资料与管理员完整编辑、可恢复成员删除 |
| `static/task-timeline.js` / `static/task-board.js` | 专业组时间轴、严格日期校验、稳定车组坐标及其他任务视图 |
| `static/theme-init.js` / `theme.js` / `themes.css` / `apple.css` | 八套主题、跟随系统首屏、账户持久化及共享组件 |
| `modules/workspace_settings.py` / `static/countdown.js` | 外观迁移、单个首页目标配置与北京时间倒计时 |
| `modules/task_dates.py` / `static/task-date-defaults.js` | 主前置截止日前两天建议、日期来源与显式重置 |
| `static/task-center.js` / `task-center.css` / `templates/_daily_projects_content.html` | 统一任务中心双标签与新建弹窗 |
| `modules/task_attachments.py`、`image_storage.py`、`attachments/` | 截图访问与存储校验 |
| `modules/discussions.py` | 本地协作话题、消息、未读、撤回与附件接口 |
| `modules/collaboration_schema.py` / `collaboration_storage.py` | 可重复协作迁移、流式原件存储及安全缩略图 |
| `modules/funds.py` | 资金、审核资格、统计图表 |
| `modules/invoices.py` | 发票 PDF/JSON、任务队列、SSE 与权限 |
| `templates/_workspace_sidebar.html`、`base.html`、`_workspace_top.html` | 统一侧栏及框架 |
| `templates/dashboard.html`、`board.html`、`manage.html`、`members.html`、`account.html` | 核心页面 |
| `templates/funds.html`、`invoices.html` | 资金与发票页面 |
| `static/task-*.js`、`manage-*.js`、`account.js` | 任务、成员及账户交互 |
| `static/funds.js`、`invoices.js`、`*.css` | 财务交互和样式 |
| `deploy/` | systemd、部署、数据库备份、发票导出、合成数据恢复演练 |
| `deploy/audit_review_data.py` | 只读历史数据诊断；不导入应用、不自动修复 |
| `modules/funds_revisions.py` / `invoice_validation.py` | 独立资金审核版本迁移、发票字段校验 |
| `tests/` | Flask/API/UI 资源与权限回归用例 |

修改 API 时，先阅读相应业务模块和 `tests/`；不要只按旧版 `app.py` 或旧版前端单文件结构推断。详细说明：[系统架构](系统架构与技术说明.md) · [开发指南](开发与优化指南.md)。
