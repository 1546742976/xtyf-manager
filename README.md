# 巡天御风管理系统

面向 RoboMaster 战队的内部管理工作台，基于 **Flask + SQLite + 原生 HTML/CSS/JavaScript**，包含任务协作、成员与组别管理、资金台账、发票归档及 GitHub 协作入口。

> **代码状态与线上状态不同**：本文说明仓库 `main` 分支已经实现的功能，不代表生产服务器已部署同一提交。生产版本应由维护者核对部署记录；本仓库不会自动升级线上数据库。

## 快速开始

需要 Python 3.13（CI 验证版本；其他 Python 版本以实际兼容性为准）。

    python -m venv .venv
    # Linux/macOS
    source .venv/bin/activate
    # Windows PowerShell: .venv\Scripts\Activate.ps1
    python -m pip install -r requirements.txt
    python app.py

访问 **http://127.0.0.1:8520**（可通过 `PORT` 修改本地端口）。首次创建空库时会创建 `admin` 账户：使用 `PROGRESS_INITIAL_ADMIN_PASSWORD` 指定独立初始密码，或者从仅当前系统账户可读写的 `initial-admin-password.txt` 获取随机初始凭据（可用 `PROGRESS_INITIAL_CREDENTIALS_PATH` 指定路径）。首次登录必须改密，交付并改密后应删除该文件。已有数据库和账户不会因此重置。

**不要将真实 `data.db`、`data/`、`secret.key`、发票或成员密码提交至 Git。**

## 已实现功能（以 main 源码为准）

| 工作区 | 主要能力 | 页面 |
| --- | --- | --- |
| 工作台 / 任务中心 | 任务统计、按人/状态/树展示、筛选搜索、CSV 导出 | `/`、`/tasks` |
| 任务管理 | 一个总负责人和多个参与者；任务树、前置依赖、版本冲突、字段修订与撤销、受控截图上传、逻辑删除 | `/manage` |
| 成员管理 | 新建/导入、资料编辑、角色、停用与恢复、随机初始密码、两套组别字典 | `/members` |
| 账户设置 | 修改自己的用户名、专业组、造车组别和密码；真实姓名由管理员维护 | `/account` |
| 资金台账 | 收支、统计、CSV、受授权的审核人审批 | `/funds/` |
| 发票中心 | PDF 上传/确认、归属控制、查重、报销状态、归档、统计与导出 | `/invoices/` |
| 权限与审计 | 管理员按成员 ID 配置资金审核/发票管理权限，分页查看业务操作记录 | `/admin/security` |
| GitHub 协作 | 仅展示仓库 Discussion/Issue 外部链接，不同步业务数据 | `/discussions` |

**组别定义**：专业组（如视觉、电控、机械、硬件、宣传、运营）表示人员技能；造车组别（如步兵、哨兵、重装、飞镖，以及自定义细分）表示实际任务归属。成员可同时属于多个专业组和多个造车组别；**每个任务只选一个造车组别**，不自动继承负责人专业组。

## 项目布局

    app.py               Flask 应用、登录、页面及基础成员接口
    common.py            SQLite 请求连接、会话和权限
    modules/             任务、字典、资料、资金、发票与附件模块
    templates/           服务器端页面模板
    static/              各工作区样式和原生 JavaScript
    deploy/              树莓派部署、备份、systemd 模板及演练
    tests/               unittest 回归测试
    docs/usage/          面向成员和管理员的操作手册
    docs/technical/      架构、开发、配置、运维与恢复

## 验证

本仓库使用 GitHub Actions 在 PR、main 分支提交及手工触发时自动执行
Python 单元/API 回归测试、前端 JavaScript 单元测试、语法检查与分支覆盖率统计。
覆盖率报告见 Actions Summary 和 python-coverage Artifact。

    python -m pip install -r requirements-dev.txt
    coverage run --branch --source=app,common,modules -m unittest discover -s tests -v
    coverage report -m
    node --test tests/js/*.test.cjs

测试使用隔离的临时 SQLite 与文件目录，不会读写生产数据。
完整功能覆盖矩阵、运行命令与不包括的浏览器 E2E 范围，见
[测试与 GitHub Actions](docs/technical/测试与CI.md)。

## 部署与数据安全

现有部署脚本适用于树莓派 systemd + Gunicorn，`deploy/deploy.sh` 从源码目录同步至运行目录，默认 **1 worker / 8 threads**。发票任务保存到主库，文件读改写使用同机跨进程锁；`deploy/gunicorn.conf.py` 在 worker 启动后接续队列。默认 worker 数量保持不变，多进程上线仍须验收。

`deploy/backup.sh` 可以分别备份进度库、资金库，并在周日打包发票，但**不包含任务截图和密钥组成的同一恢复点**。生产升级之前应暂停写入，对所有数据库、附件、发票和密钥制作一致性备份并先演练恢复。详见[发布检查与数据恢复](docs/technical/发布检查与数据恢复.md)。

现行服务使用 HTTP；HTTP 不提供传输加密，限制访问网络、权限和敏感数据传播是部署方必须处理的风险。这里不新增 HTTPS 开发任务。

## 文档导航

从 **[文档中心](docs/README.md)** 开始。

- [成员使用手册](docs/usage/成员使用手册.md) · [管理员操作手册](docs/usage/管理员操作手册.md)
- [系统架构](docs/technical/系统架构与技术说明.md) · [源码导读](docs/technical/source-guide.md) · [开发指南](docs/technical/开发与优化指南.md)
- [配置与权限参考](docs/technical/配置与权限参考.md) · [服务器运维](docs/technical/服务器运维手册.md) · [发布检查与数据恢复](docs/technical/发布检查与数据恢复.md)
- [当前待办](TODO.md) · [设计规范](DESIGN.md)

详细实现与数据迁移行为以当前代码和测试为准，历史重构记录不再作为现行功能说明。
