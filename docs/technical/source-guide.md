# 源码导读（按功能定位）

当前执行入口为仓库根目录的 `app.py`，不再使用旧“源代码注释版”副本。

| 路径 | 主要职责 |
| --- | --- |
| `app.py` | Flask 初始化、入口页、登录/退出、初始数据库、导出及基础成员接口 |
| `common.py` | `PROGRESS_DB`、请求级数据库连接、用户会话与权限装饰器 |
| `modules/task_api.py` | 任务 API 路由 |
| `modules/task_mutations.py` / `task_validation.py` | 任务新增、更新及输入合法性校验 |
| `modules/task_queries.py` / `task_types.py` | 看板数据组织与共享类型 |
| `modules/task_delete.py` / `task_revisions.py` | 逻辑删除、字段修订与撤销 |
| `modules/task_migrations.py` / `task_catalogs.py` | 版本化迁移、两套组别字典及成员关联 |
| `modules/member_profiles.py` | 个人资料与管理员完整编辑、可恢复成员删除 |
| `modules/task_attachments.py`、`image_storage.py`、`attachments/` | 截图访问与存储校验 |
| `modules/discussions.py` | GitHub Issue/Discussion URL 外链 |
| `modules/funds.py` | 资金、审核资格、统计图表 |
| `modules/invoices.py` | 发票 PDF/JSON、任务队列、SSE 与权限 |
| `templates/_workspace_sidebar.html`、`base.html`、`_workspace_top.html` | 统一侧栏及框架 |
| `templates/dashboard.html`、`board.html`、`manage.html`、`members.html`、`account.html` | 核心页面 |
| `templates/funds.html`、`invoices.html` | 资金与发票页面 |
| `static/task-*.js`、`manage-*.js`、`account.js` | 任务、成员及账户交互 |
| `static/funds.js`、`invoices.js`、`*.css` | 财务交互和样式 |
| `deploy/` | systemd、部署、数据库备份、发票导出、合成数据恢复演练 |
| `tests/` | Flask/API/UI 资源与权限回归用例 |

修改 API 时，先阅读相应业务模块和 `tests/`；不要只按旧版 `app.py` 或旧版前端单文件结构推断。详细说明：[系统架构](系统架构与技术说明.md) · [开发指南](开发与优化指南.md)。
