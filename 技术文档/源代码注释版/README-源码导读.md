# 巡天御风管理系统 · 源代码注释版 · 导读

本目录是项目**全部源代码的带注释版本**，与项目源码目录保持同步（注释仅用于阅读，不影响运行）。

- 项目源码目录（开发时修改这里）：`D:\Robotmaster\管理\progress_system\`
- 线上运行目录（树莓派）：`/opt/rm-progress/`

---

## 1. 文件清单与对应关系

| 本目录文件 | 项目中的位置 | 说明 |
|---|---|---|
| `app.py` | `app.py` | 主程序：应用入口 + 进度系统全部路由 |
| `common.py` | `common.py` | 公共模块：数据库、登录态、权限装饰器 |
| `modules/funds.py` | `modules/funds.py` | 资金管理模块（Blueprint `/funds`） |
| `modules/invoices.py` | `modules/invoices.py` | 发票管理模块（Blueprint `/invoices`，含内嵌前端） |
| `modules/__init__.py` | `modules/__init__.py` | 包标识 |
| `templates/*.html` | `templates/` | 页面模板（base=通用布局，login/board/manage，funds=Bootstrap 页） |
| `static/style.css` | `static/style.css` | 全局样式（顶栏/看板/徽章/弹窗/移动端） |
| `static/app.js` | `static/app.js` | 看板前端逻辑（轮询/渲染/弹窗/角标） |
| `deploy/deploy.sh` | `deploy/deploy.sh` | 一键部署脚本 |
| `deploy/backup.sh` | `deploy/backup.sh` | 每日备份脚本（cron 调用） |
| `deploy/export_invoices.sh` | `deploy/export_invoices.sh` | 发票整包导出（命令行） |
| `deploy/rm-progress.service` | `deploy/rm-progress.service` | systemd 服务模板 |
| `deploy/seed_data.py` | `deploy/seed_data.py` | 成员/任务初始化导入脚本 |
| `deploy/frpc.toml` | 线上：`/opt/frp/frpc.toml` | frp 隧道配置（本目录为脱敏展示版） |
| `requirements.txt` | `requirements.txt` | Python 依赖清单 |
| `start.bat` | `start.bat` | Windows 本地调试启动 |

## 2. 建议阅读顺序

1. `app.py` —— 从文件头的模块说明开始，看初始化 → 登录 → 看板 API → 任务 API
2. `common.py` —— 理解登录态与权限装饰器（其他模块都依赖它）
3. `modules/funds.py` —— 最标准的"BluePrint 模块"范例（含审核流与图表）
4. `modules/invoices.py` —— 体量大，先看文件头"集成适配说明"，再看各 API 前的注释；前端部分在 `create_index_html()`
5. `static/app.js` + `templates/board.html` —— 理解看板的轮询与渲染
6. `deploy/*` —— 理解部署、备份、隧道

## 3. 注释规范

- 文件头：该文件的职责、运行方式、关键约束（如"单 worker"）
- 函数/段落前：`# ---- xxx ----` 单行说明（Python / Shell）或 `// ===== xxx =====`（JS）
- HTML 模板：`<!-- xxx -->` 或 Jinja 注释 `{# xxx #}`
- 行业知识：如"剩余天数计算规则""审核流规则""权限矩阵"等直接写在相关代码旁

## 4. 修改注意事项

| 改动类型 | 生效方式 |
|---|---|
| `.py` 代码 | 必须走 `deploy/deploy.sh`（重启服务） |
| `templates/` 模板 | 同上（生产模式 Jinja 不热重载） |
| `static/` 的 css/js | 直接覆盖线上对应文件即可（浏览器 Ctrl+F5） |
| 发票页前端 | 注意它内嵌在 `modules/invoices.py` 里，改完要过语法检查 |
| 数据库结构 | 用 `PRAGMA table_info` + `ALTER TABLE` 做兼容迁移（参考 funds.py） |

## 5. 与线上代码的关系

- 本目录的代码与项目源码**完全一致**（含全部注释），可直接对照线上逻辑阅读
- 如果直接用本目录文件覆盖线上（不推荐直接覆盖，推荐走 deploy.sh），行为不变
