# 测试与 GitHub Actions

## 本地运行

项目使用 Python 3.13 / Node.js 22。后端以标准库 unittest 测试；
前端采用 Node.js 自带的 node:test 和 vm，无需 npm 依赖。

```sh
python -m pip install -r requirements-dev.txt
python -m compileall -q app.py common.py modules tests deploy
coverage run --branch --source=app,common,modules,deploy -m unittest discover -s tests -v
coverage report -m --fail-under=65
coverage html
for file in static/*.js; do node --check "$file"; done
node --test tests/js/*.test.cjs
```

## 自动检查

.github/workflows/ci.yml **仅在目标分支为 main 的 PR 创建或更新时运行**；直接推送、提交或合并到 main 不会触发该工作流，也不再自动响应 workflow_dispatch。
Python 回归测试按文件所属模块拆成 6 个互斥 shard：`tasks`、`members`、`finance`、`assets`、`collaboration`、`platform`，每组都在 Ubuntu/Windows 上并行执行。分组由 `tests/ci_shards.py` 维护，自动收集全部 `tests/test_*.py`；新增文件如果不匹配任何已知前缀，则归入 `platform`，避免漏测。原有发票和资产专项回归包含在对应 shard 中，不再在全量测试之前重复执行。每个 shard 独立运行 `coverage run --parallel-mode --branch`，用独立 Artifact 保存 `.coverage.*`。

`python-coverage` 汇总 Job 在每个平台下载对应所有 shard 的数据，经 `coverage combine` 合并后统一运行分支覆盖率报告和 **65%** 门槛检查；生成按系统区分的 `python-coverage-<os>` XML Artifact 和 Actions Summary，**不是按单一 shard 的覆盖率设置门槛**。Python 语法、Ruff 和精确 main 基线的 SQLite 升级检查保留为独立的 Ubuntu/Windows 双平台 Job。

JavaScript 单元测试仍单独执行；当前 **20 个浏览器测试脚本**按基础功能、任务、UI/客户端、资产拆成 10 组并行 Job，包含 `ui_regressions`、`assets_intake` 和 `assets_catalog_paging`，保留 Chromium、WebKit、测试失败判定及每组独立的诊断 Artifact。`diagnostics.cjs` 和 `asset_*_checks.cjs` 为套件调用的辅助模块，不计作独立套件。所有测试分组均须成功才能合并。

本地可执行任意 Python 分组，例如 `python tests/ci_shards.py tasks`（不需要 GitHub Actions 环境）；完整本地覆盖率仍使用上文 `unittest discover` 命令。

### PR 中的真实旧版本升级测试

Python 两个平台都会额外在 `.ci-main-baseline/` 检出 **`github.event.pull_request.base.sha`**（PR 提交时 main 的精确 commit，而非当前开发分支的模拟结构），再执行 `python tests/upgrade_from_main.py`。

测试先在该 main 快照中真正导入旧 `app.py` 初始化隔离的 SQLite 数据库，写入已验证且暂停的邮箱、未验证且有待处理验证码/旧链接的邮箱、发送中队列及既有团队项目；之后切换到当前 PR 的 `app.py`，在**同一数据库文件和密钥**上升级。核查 schema、收件人状态、已发送去重记录、未完成挑战、老验证链接失效（升级到六位码时）、新验证码 API 操作、数据库完整性和重复启动迁移的幂等性。

如果 main 已经支持六位数字验证码，测试改为验证旧有效验证码在升级后继续保留；这使得测试可以用于以后的 PR，不必一直假定 main 是链接验证时代的旧版本。整个过程不会发送真实 SMTP 邮件，也不会触碰线上服务器或生产数据库。此步骤只在 PR 检查中执行，不会在本地常规 unittest discover 时自行运行。


## 本轮缺陷回归与浏览器检查

`tests/test_review_fixes.py` 覆盖审核版本、迁移、非法字段、重复脱敏、嵌套整理、确认值、特殊路径、初始化导入和只读诊断；`tests/js/invoice_security.test.cjs` 验证安全事件委托及单次编码。

浏览器专项使用真实 Chromium 和独立临时 Flask 服务：

```sh
# 已安装项目依赖，并已安装 Playwright/Chromium 时运行
node tests/browser/review_fixes.cjs
node tests/browser/pr22_merge.cjs
```

可用 `REVIEW_PYTHON` 指定 Python，`NODE_PATH` 指向固定 Playwright **1.57.0** 的包目录。Bootstrap 5.3.3、图标字体和许可由服务端 `/vendor/` 白名单本地提供，与客户端 vendor-lock 校验同源；不再使用 CDN mock 或外部 CDN 回退。客户端继续使用自己的 `dist/vendor`。`ui_regressions.cjs` 在阻断外部网络的情况下覆盖资金操作、发票原生 dialog、中文错误页、输入限制、成员导入、主题和移动触控。

测试数据库、密钥、资金目录、发票目录和任务附件目录均设为隔离的测试临时路径；
不使用生产数据、生产服务或 GitHub Secrets。

`pr22_merge.cjs` 专门覆盖主题持久化与上游进度、时间轴、依赖表单及本地协作页面的整合行为。本次基线、冲突处理及实际结果见 [PR22 合并验证](PR22合并验证.md)。

## 功能覆盖矩阵

| 模块 | 已有与新增测试 |
| --- | --- |
| 登录与账户 | 登录限流、CSRF、首次改密、会话失效、个人资料和管理员编辑 |
| 成员 | 添加/导入、删除/恢复、权限、角色、专业组和造车组别、前端筛选 |
| 任务 | 创建/修改/删除、任务树、依赖环、版本冲突、修订撤销、参与者和筛选 |
| 附件 | 图片校验、身份权限、存储与写入故障回滚 |
| 资金 | 按分舍入、支出校验、分类统计、筛选排序、CSV 导出、审核与复核 |
| 发票 | 上传确认、归属、查重、文件移动、金额缓存、统计与导出、后台任务恢复 |
| 安全与恢复 | CSV 公式注入、审计脱敏、密钥、迁移、离线备份恢复 |
| Web UI | 工作区资源、成员筛选、任务筛选与树、金额格式、文本转义 |

后端 tests/test_*.py 是 Flask API/SQLite 回归及恢复故障演练；
tests/test_domain_units.py 直接对领域规则开展单元测试；
tests/test_feature_contracts.py 验证跨模块 HTTP 合约；
tests/js/*.test.cjs 在模拟环境中运行前端纯逻辑。

**边界：** 这些测试不能替代真实浏览器 E2E（文件选择器、原生 dialog、CSS 布局）
或生产环境并发/多进程负载测试。覆盖率不是功能正确性的证明，后续应根据
coverage report -m 的未覆盖行和分支继续补测。

## 2026-10-22 验收范围

现有 17 组场景加 `ui_regressions` 共 18 组；`diagnostics.cjs` 是辅助模块，不计作套件。完整命令、当前统计及平台边界见 [本轮修复记录](全项目审查修复-2026-10-22.md)。CI 保持 Node 22；本地验证须记录实际 Node 版本。Python 与客户端 Python Playwright 均固定 1.57.0。
