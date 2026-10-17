# 测试与 GitHub Actions

## 本地运行

项目使用 Python 3.13 / Node.js 22。后端以标准库 unittest 测试；
前端采用 Node.js 自带的 node:test 和 vm，无需 npm 依赖。

```sh
python -m pip install -r requirements-dev.txt
python -m compileall -q app.py common.py modules tests deploy
coverage run --branch --source=app,common,modules -m unittest discover -s tests -v
coverage report -m
coverage html
for file in static/*.js; do node --check "$file"; done
node --test tests/js/*.test.cjs
```

## 自动检查

.github/workflows/ci.yml **仅在目标分支为 main 的 PR 创建或更新时运行**；直接推送、提交或合并到 main 不会触发该工作流，也不再自动响应 workflow_dispatch。
Python 使用 Ubuntu/Windows 双平台矩阵，JavaScript 使用独立 job，任何 job 失败即 CI 失败。
Python 使用分支覆盖率统计，结果写入 Actions Summary，并上传 coverage.xml Artifact。
Artifact 名称按系统区分，覆盖率门槛继续保持 65%。

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

可用 `REVIEW_PYTHON` 指定 Python，`NODE_PATH` 指向已有 Playwright 包目录。若 CDN 无法访问，可将同版本 Bootstrap 5.3.3 的真实 `bootstrap.bundle.min.js` 路径设置为 `REVIEW_BOOTSTRAP_PATH`；此选项仅替换浏览器测试中的 CDN 响应，不改变项目依赖。测试覆盖真实登录改密、文件选择上传、确认值、XSS、预览、过期审核和草稿选择保留/失效拦截，不代表生产负载或完整浏览器 E2E。

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

**边界：** 这些测试不能替代真实浏览器 E2E（文件选择器、Bootstrap 弹窗、CSS 布局）
或生产环境并发/多进程负载测试。覆盖率不是功能正确性的证明，后续应根据
coverage report -m 的未覆盖行和分支继续补测。
