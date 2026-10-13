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

.github/workflows/ci.yml 在提交任意 PR、向 main 推送及手工 workflow_dispatch 时运行。
Python 和 JavaScript 两个独立 job 并行执行，任何 job 失败即 CI 失败。
Python 使用分支覆盖率统计，结果写入 Actions Summary，并上传 coverage.xml Artifact。

测试数据库、密钥、资金目录、发票目录和任务附件目录均设为隔离的测试临时路径；
不使用生产数据、生产服务或 GitHub Secrets。

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
