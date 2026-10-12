# 巡天御风管理系统

基于 Flask + SQLite 的 RoboMaster 战队管理平台，整合任务进度、成员、资金台账和发票归档。

## 项目结构

```text
app.py                Flask 入口、任务与成员 API
common.py             SQLite 连接、用户会话和权限
modules/              资金与发票 Blueprint
templates/            看板、管理、登录、资金、发票页面
static/               看板 JavaScript 与通用 CSS
deploy/               树莓派部署、备份与 systemd 模板
docs/usage/           成员与管理员操作手册
docs/technical/       架构、运维与开发文档
tests/                Flask API 回归测试
```

原 `技术文档/源代码注释版/` 中的代码现已提升到仓库根目录，不再维护两个源码目录。发票页面已从 `modules/invoices.py` 拆到 `templates/invoices.html`，继续由 Python 读取并注入登录角色信息。

## 本地启动

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\\Scripts\\activate
python -m pip install -r requirements.txt
# 建议先将 PROGRESS_INITIAL_ADMIN_PASSWORD 设置为独立强密码
python app.py
```

访问 http://127.0.0.1:8520。首次建库时用户名为 `admin`，未指定 `PROGRESS_INITIAL_ADMIN_PASSWORD` 时自动生成随机初始密码并在初始化日志中打印一次。已有数据库、账号不会被重置。

## 验证

```bash
python -m compileall -q app.py common.py modules tests
python -m unittest discover -s tests -v
for file in static/*.js; do node --check "$file"; done
```

线上仍使用 `gunicorn --workers 1 --threads 8 app:app`，运行目录为 `/opt/rm-progress`。部署前务必备份 `data.db`、`data/`、`secret.key`。不要把真实账号、数据库和发票提交到仓库。

## 协作任务升级（实现完成，待部署）

协作任务模块已完成实现和浏览器流程验证，仍待最终源码审查与生产部署。它保留现有 Flask、SQLite、入口与单 worker 部署方式，并在旧任务数据上自动执行**版本化、事务化且可重复运行**的迁移：

- 多组别成员与任务、单项目归类；旧的组别和项目名称只会去首尾空格，绝不按字符猜测或拆分。
- 一个总负责人和多个参与者。负责人或参与者可编辑除父任务外的任务设置；只有管理员可维护父任务关系。
- 独立的任务树与依赖关系。树用于层级展示；依赖只给出开始日期建议和风险提示，不会自动改日期。
- 带版本号的并发保护、逐字段修订记录和管理员按变更撤销。若之后有人改过同一字段，撤销会被拒绝，避免覆盖新内容。
- 单次最多上传 5 张、每张不超过 10 MiB 的 PNG/JPEG/WebP 工作截图；文件受登录和任务权限保护，不会放到公开静态目录。
- GitHub Issue/Discussion 仅作为外部链接入口。是否公开仓库及具体链接由部署配置决定。

上线前不应把这些能力视为生产可用；当前线上行为仍以已部署页面为准。

## 升级前的备份与恢复

当前 `deploy/backup.sh` 的 cron **不会备份** `data/task_attachments/`，也不会形成包含密钥、资金、发票与截图的单一恢复点，因此不能单独作为协作升级后的完整恢复方案。升级生产环境前，必须暂停写入（推荐停止服务），将进度数据库及其 `-wal`/`-shm`、`data/funds/`、`data/invoices/`、`data/task_attachments/` 和 `secret.key` 作为一个恢复点完整保存；或者在暂停写入期间用 SQLite 在线备份 API 备份每个数据库并复制同一时点的全部文件。恢复到隔离目录后先执行 `PRAGMA integrity_check` 和内容核对，再切换服务。仓库内的演练只使用合成数据库、合成附件和临时密钥，绝不读取真实数据库、发票或密钥。

## 重构规划与功能状态

详细功能清单、P0/P1 缺陷、UI 规范、接口权限矩阵和部署验收表见 [功能审计与重构设计](docs/technical/重构规划与功能审计.md)。

本次引入 `static/ui.css`，并将管理、资金和发票页面的 JS 拆成 `static/manage.js`、`static/funds.js`、`static/invoices.js`，新增 `static/account.js` 和 `templates/account.html` 统一账户入口；页面由独立 CSS/JS 组织，便于持续维护。

资金审批仅允许配置在 `FUNDS_REVIEWERS` 环境变量内的管理员/项管操作（默认 `chy`）。修改已审核记录会使其重新待审。新增 `/invoices/api/export/download` 提供直接下载 CSV，原 JSON 导出接口保持兼容。

新成员与批量导入改为随机独立初始密码（可显式设置至少 8 位的密码）；操作成功时初始密码仅在界面响应中显示，请安全传达。

## 第三轮安全与可靠性修复

发票临时上传现在按成员隔离，确认、清理和元数据修改均强化了后端权限和格式校验；登录增加失败限流，密码重置和成员状态变动可注销旧会话。完整变更、向后兼容说明和部署注意见 [第三轮修复说明](docs/technical/第三轮安全与可靠性修复.md)。

## 仍待完善

- CSRF 防护、关键业务审计日志、HTTP 部署环境的访问边界和网络隔离措施（不规划 HTTPS 功能）。
- 资金审核目前仍依赖固定用户名 `chy`；建议改为独立审核权限。
- 发票 SSE 后台任务存进程内，暂时必须单 worker。
- 新建/批量导入/重置已默认随机初始密码；仍需安全分发与首次登录强制改密。
- 财务和发票页面的全面 UI 组件统一、实际移动浏览器适配需继续完成。

[成员手册](docs/usage/成员使用手册.md) · [管理员手册](docs/usage/管理员操作手册.md) · [架构说明](docs/technical/系统架构与技术说明.md) · [开发指南](docs/technical/开发与优化指南.md) · [源码导读](docs/technical/source-guide.md)。
