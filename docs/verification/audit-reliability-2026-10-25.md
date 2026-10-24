# 审计缺陷修复与可靠性验证（2026-10-25）

## 范围与交付

- 基线：`9f04d244272db5b1076c2a99e20e45ee37453640`。
- 分支：`codex/audit-reliability-fixes`；独立工作区：`D:/缓存/audit-reliability-fixes/xtyf-manager`。
- 保留原开发分支、其他源码工作区及手工验收站点。没有推送、部署、打包、改版本或调整历史业务数据。
- 测试只使用临时数据库、合成账户、生成的图片/PDF，以及禁止实际投递的邮件测试。恢复实验复制的是刚创建的合成测试库，不是业务库。
- 沿用现有依赖；Linux 验证环境按仓库 requirements 安装，没有增加运行依赖。

| 批次 | 提交 | 内容 |
|---|---|---|
| 1 | `e528805` | 密码重置事务内重新授权；密码类型与长度；SQLite ID、角色参数边界；表单限制及反例 |
| 2 | `9cad32b` | 资金创建回执、幂等迁移、提交后同步警告、持久化重试、网站与客户端页面验证 |
| 3 | 本报告所在提交 | 隔离可靠性实验；补齐创建成员的角色类型校验；兼容测试断言和时序；最终验收记录 |

第三批提交号可用 `git log -1 --format="%h %s" -- docs/verification/audit-reliability-2026-10-25.md` 查询。前两批分别为 9 文件（+176/-22）、18 文件（+596/-53）。实际修改可用以下命令审阅：

```bash
git show --stat e528805
git show e528805 -- app.py modules/input_validation.py modules/task_catalogs.py
git show --stat 9cad32b
git show 9cad32b -- modules/funds.py modules/funds_requests.py static/funds-create.js static/funds.js
git diff 9f04d24..HEAD --stat
git diff 9f04d24..HEAD
```

## 修复行为

### D2、D3、D4

密码重置先读取目标、在写事务外计算哈希，再 `BEGIN IMMEDIATE`；通过 `current_user()` 重新查询操作者状态、会话版本和角色，并重查目标角色、删除/启用状态与会话版本。检查和密码更新同一事务完成。目标升级期间项管的旧权限不能继续生效；操作者停用则拒绝。本人改密仍需原密码，首次改密和其他会话失效规则保留。

密码统一为字符串、8–1024 字符，不截断或剥除空格，自动生成初始密码保留。非法角色类型、超出 SQLite 范围的 ID 返回中文 400，合法不存在的 ID 返回 404。第三批补充了创建成员接口的角色类型拒绝，先以数组/对象/布尔值请求得到 200 复现，再修为 400。项管创建账号仍不能提升为管理员。

对应反例：`tests/test_audit_reliability.py`，覆盖哈希期间目标升级、操作者停用、1024 字符往返、1025 字符拒绝、非字符串密码、首次改密门禁、角色类型和超大 ID。

### D1：资金新增与结果恢复

| 接口/行为 | 契约 |
|---|---|
| `GET /funds/api/capabilities` | 沿用资金鉴权；返回 `create_idempotency: 1` 和当前 `member_id` |
| `POST /funds/api/records` | 必须有 16–100 位 `[A-Za-z0-9_-]` 的 `request_id` |
| 缺少/非法标识 | 400，`reason=idempotency_required`，提示刷新适配页面或升级客户端 |
| 同账号、同标识、同正文 | 返回原回执，`replayed=true`，不再创建 |
| 同账号、同标识、不同正文 | 409，`reason=request_id_conflict` |
| 新页面检测账号切换 | 携带 `expected_member_id`；与当前账户不同返回 403 `account_changed` |
| 已提交但文本台账同步失败 | 成功回执加 `ledger_sync=failed`，明确“记录已保存，台账同步失败” |

现有资金库新增 `funds_requests`，主键为 `(actor_id, request_id)`。资金记录和包含原始 ID、金额、版本等信息的回执同事务提交；摘要不注入当前时间、角色或姓名默认值。同标识重试返回原创建回执，修改/删除资金记录不会清除回执。迁移可重复执行，不重建原表、不清理历史重复资金记录。

台账继续采用线程串行和原子替换；等锁上限 5 秒。编辑、删除、审核也区分“数据库已提交”和“文本同步失败”。同步日志只含阶段、请求标识、记录 ID、异常类型，不写完整财务正文。相同创建请求重试可以重新同步台账。

网站和客户端共用 `static/funds-create.js`：

1. 按服务器地址和成员 ID 检查能力并恢复本地待确认请求。
2. 生成标识、保存完整原请求并读回验证；存储失败不发送。
3. 提交期间禁重复点击；未知结果时暂停新建、保留原正文和标识。
4. 网络错误、5xx、HTML、截断 JSON、结构不完整、回执标识不符均进入“结果待确认”。刷新后仍可手动重试原请求。
5. 认证/权限变化保留请求，要求原账号核对；不同账号、服务器不复用它。
6. 有效成功回执或明确的参数业务拒绝才能清理待确认记录。页面表示“该创建请求已处理”，最新台账状态另读，不把旧回执当成当前记录状态。

新页面连接没有能力接口的旧服务器会暂停新增，其他既有操作保留。已安装旧客户端须获得适配页面后才能新增；本轮源码提交不等于已发布页面。

## 验证结果

| 环境/检查 | 实际结果 |
|---|---|
| Windows Python 3.11.8 完整回归 | 660 项，通过，6 项平台相关跳过；分支覆盖率 80% |
| 最后补充的创建角色边界 | Windows 专项 5 项通过；也包含在随后 Linux 全量结果中 |
| Ubuntu WSL Python 3.13.16 最终完整回归 | 660 项全部通过，无跳过；591.749 秒；分支覆盖率 80%，超过 65% 门槛 |
| 编译 / Ruff | Python 编译检查与 `ruff check --no-cache app.py common.py modules deploy tests` 通过 |
| JavaScript / Node 22 | 全部 135 项通过 |
| 精确旧库升级 | `XTYF_UPGRADE_BASE_SHA=9f04d244272db5b1076c2a99e20e45ee37453640`，1 项实际旧代码建库升级测试通过 |
| Windows Playwright 1.57.0 | 原有 21 组 + `funds_recovery`，最终逐项通过；包含 Chromium 和现有 WebKit 场景 |
| Linux Playwright 1.57.0 | 同样 22 组最终逐项通过；WSL WebKit 使用测试进程的软件渲染设置，非远端 CI 容器验收 |
| 客户端模板 | Windows、Linux 均构建 27 个模板、290 个资源；未生成安装包 |
| 共享原生桥接逻辑 | 11 项通过 |
| 打包页面 + 模拟 IPC + 实际隔离后端 | Windows、Linux 各 31 项通过；无页面错误、无远程网页请求 |
| Android Java 传输/来源策略 | Linux JDK 编译及执行，89 项断言通过；不是 Android APK 或真机验收 |

22 组场景：`review_fixes`、`pr22_merge`、`action_failures`、`login_mobile`、`task_center`、`task_schedule`、`daily_mail`、`task_profiles`、`seven_repairs`、`seven_repairs_review`、`palette_workspace`、`local_collaboration`、`account_avatar`、`ui_regressions`、`funds_recovery`、`account_invoice_ui`、`client_updates_avatar`、`client_connection`、`assets`、`assets_serialized`、`assets_intake`、`assets_catalog_paging`。辅助模块没有计入套件数。

新增资金浏览器反例会先让真实隔离后端提交，再替换返回为 HTML、截断 JSON、无效结构、不匹配标识或 503。刷新后验证原正文不变、仅一条资金记录；另验证存储失败不发 POST、旧服务端只禁新增。打包页面测试覆盖模拟原生通道在已提交后丢失响应、重新打开页面和同标识重试。

### 首次失败、重跑与限制

- Windows 受限进程首次旧回归出现 `PermissionError`（`mimetypes` 读取注册表）、浏览器 `ERR_NETWORK_ACCESS_DENIED`；在获准的隔离测试进程重跑后通过。未修改产品代码绕过系统访问规则。
- WSL 首个 `/tmp` 验证目录在会话结束后消失，启动报 `cd: .../source: No such file or directory`；改用新建的项目专属缓存子目录。没有触碰既有工具链、其他项目或生产数据。
- Linux 第一次完整 Python 回归在 `test_permissions_match_roles_and_independent_finance_grants` 的三个角色子项得到 `401 != 200`；该模块独立 14 项通过，最终完整 660 项通过。首次失败尚未确定根因，不能宣称已修复；保留为测试稳定性跟踪项。
- WSL WebKit 首轮多次 `Target crashed`。仅对测试进程设置 `WEBKIT_DISABLE_COMPOSITING_MODE=1 LIBGL_ALWAYS_SOFTWARE=1` 后崩溃场景通过，没有更改产品启动顺序、依赖版本、断言或超时。软件渲染轮中协作与头像加载分别超时；各自独立复核通过，初次结果仍保留。
- `ui_regressions` 等待旧资金成功提示，改为等待新的创建回执提示；`assets` 对未首次改密账号错误地断言有效权限，改为检查独立授权且有效权限仍为空；`task_center` 在 URL 改变后立即检查异步加载按钮，改为先等待同一按钮可见，仍断言唯一且可操作，未延长超时。
- 故障注入主动制造的 SQLite 回执写入失败、文件替换失败和发票快照失败会产生预期错误日志；不计为正常路径的意外失败。
- 没有运行远端 GitHub CI、生产压力测试、真实邮件、Windows WebView2 原生程序、Android 真机、相机/扫码枪/打印机或完整 Rust/Android 应用构建。不用模拟 IPC 或 Java 传输测试替代这些结论。
- 此轮精确升级基线是用户指定的修复起点，不冒充尚未创建的新 PR 的 `base.sha`；将来发 PR 时须使用其实际基准重新核对。

## 隔离实验与后续风险

实验入口：`tests/reliability_experiments.py`。SSE 使用 `tests/reliability_probe_app.py`，必须提供带 `.synthetic-only` 标记的新测试目录；真实发票 SSE 路由运行在 Gunicorn 26.2.0、1 worker / 8 threads 下，工作任务不执行。部署实验将 sudo、服务操作、安装和 curl 全部替换为临时目录内模拟命令，不实际部署。

| 方向 | 本次证据与结果 | 后续动作（本轮未改） |
|---|---|---|
| 代理限流 | 同一模拟 peer 的第 40 次错误登录被拒；即使变换伪造 X-Forwarded-For，正常账号随后仍为 429；另一 peer 正常登录 200 | 确认生产代理层数、来源地址和可信边界，再制定真实客户端 IP 配置及共享出口策略 |
| SSE 容量 | 0 条 SSE：登录页约 20.1 ms、普通 API 3.3 ms；7 条：5.9/2.9 ms；8 条：两者均超过本实验 2 秒超时；关闭流后约 26.15 秒恢复 | 先确认峰值在线/标签页数，再评估连接上限与断开策略、线程余量；不要直接用增加 worker 掩盖问题，台账锁等进程边界也须核查 |
| 完整恢复 | 使用实际接口产生主库、资金回执、发票及归属元数据、头像和任务图片；离线合成副本快照恢复到空目录，11 个文件哈希一致，两库 integrity_check=ok，业务引用存在 | 仍需运维确认生产备份位置、停写方式、恢复授权和演练窗口；本次服务状态检查为离线合成副本模拟 |
| 旧部署入口 | `deploy/deploy.sh` 的模拟 HTTP 500 和 curl 连接失败均退出 0 并打印完成；rsync 中途失败退出 23，重启失败退出 1，均不打印完成 | 单独修复旧健康检查失败判定；本轮仅记录，不改部署脚本 |
| 时间 | 同一固定时刻 2026-10-24 17:00Z：模拟 UTC 主机的 `common.now_str()` 为 10-24 17:00，`assets.stamp()` 为 10-25 01:00；服务模板未固定 TZ | 现场确认服务器/容器时区和业务日期定义，先制定迁移策略，不平移历史记录或修改本机时区 |
| HTTP/HTTPS | 桌面 `website.rs::discovery_redirect` 静态核对同主机升级、端口和降级拒绝；Android 89 项包含对应 URI 策略、跨源拒绝和写请求不重放；桥接验证实际连接地址识别 | 生产最终协议、证书链和反向代理拓扑待现场验证；本轮没有强制 HTTPS 或放宽证书校验 |

这些测量是单机合成实验，不是生产 SLA 或容量保证。实验得到的风险未被自动扩展为产品改动。

| 待确认事项 | 建议责任人/最小下一步 |
|---|---|
| RPO / RTO | 系统负责人确认可接受的数据损失窗口和恢复时长，再量化演练 |
| 异地备份 | 运维确定加密副本位置、访问权限与定期恢复验证 |
| 密钥轮换 | 发布负责人列出服务密钥、签名密钥、SMTP 凭据和轮换/撤销流程 |
| 数据留存 | 业务与运维确认财务回执、待确认本地请求、审计、附件的保留期限；本轮不自动清理 |
| 依赖漏洞扫描 | 维护者安排现有 Python/Node/Rust/Android 依赖的定期扫描与处理责任；本轮未声称完成漏洞扫描 |
| 告警 | 运维确认 SSE 饱和、资金台账同步失败、备份失败的接收人与处置时限 |
| 测试稳定性 | 维护者跟踪 Linux 首轮客户端权限 401 及 WebKit 加载时序，保留失败和重跑证据 |
| 原生验收 | 客户端负责人安排真实设备登录、相机、扫码和打印；发布窗口另行确认 |

## 复现命令与回滚

在隔离源码工作区、已安装仓库开发依赖的环境运行。测试 fixture 自动创建临时业务路径。Linux 浏览器使用 Node 22、Playwright 1.57.0；WSL 本次额外设置的软件渲染变量只作用于测试进程。

```bash
python -m compileall -q app.py common.py modules tests deploy
ruff check --no-cache app.py common.py modules deploy tests
python -m coverage run --branch --source=app,common,modules,deploy -m unittest discover -s tests -v
python -m coverage report --fail-under=65
node --test tests/js/*.test.cjs
node tests/browser/funds_recovery.cjs
python clients/build_frontend.py
node clients/frontend/test_bridge.cjs
python clients/frontend/test_browser.py
python clients/android/test_transport.py
python tests/reliability_experiments.py --sse --output .build-tools/audit/reliability.json
```

Windows 应使用确认可运行的解释器，并设置 `REVIEW_PYTHON`、`NODE_PATH`、`PLAYWRIGHT_BROWSERS_PATH`；不要把 Windows 3.11 结果当成 CI 3.13。全量浏览器名称见上表，可按 `.github/workflows/ci.yml` 中套件列表执行。临时输出保留在 Windows 项目 `.build-tools/audit-runtime` 与 WSL 的本次 `audit-reliability.*` 目录，未加入 Git。

逐批回滚：`git revert e528805`、`git revert 9cad32b`，或回滚第三批报告所在提交。若完整撤销，应按第三批、第二批、第一批的逆序执行，保留后续其他工作，冲突逐项处理。

代码回滚不删除 `funds_requests` 或任何业务记录，不执行逆向数据迁移、不自动恢复备份。**回滚资金幂等实现会重新暴露旧版重复新增风险**。后续正式发布需协调服务端和适配页面；当前安装客户端并未因本轮本地提交自动更新。
