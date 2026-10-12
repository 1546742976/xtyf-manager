# 源代码导读

原 `技术文档/源代码注释版/` 的可运行代码已迁至仓库根目录，避免维护两份源码。

| 路径 | 职责 |
| --- | --- |
| `app.py` | 登录、成员、进度任务与数据接口 |
| `common.py` | 请求级数据库连接、当前用户及权限 |
| `modules/funds.py` | 资金模块、统计和审批 |
| `modules/invoices.py` | 发票模块、文件管理、后台任务 |
| `templates/board.html`、`templates/manage.html` | 看板和任务管理 |
| `templates/funds.html` | 资金管理页面 |
| `templates/invoices.html` | 发票管理页面，保留原先的角色注入占位符 |
| `static/app.js`、`static/style.css` | 进度前端行为与样式 |
| `deploy/` | 一键部署、备份、systemd 配置 |

业务接口与运行环境说明参见仓库根目录 `README.md` 和 [开发与优化指南](开发与优化指南.md)。本次目录重排不修改原有数据路径和 API。
