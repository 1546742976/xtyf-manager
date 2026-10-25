# 剩余可靠性修复验收（2026-10-25）

基线 `72643c1`，分支 `codex/audit-reliability-fixes`。保留此前 D1–D4。
本轮没有推送、部署、打包、改版本、修改验收站点或真实业务数据；未新增业务表或依赖。

## 改动与本地提交

| 批次 | 提交 | 实际内容 |
|---|---|---|
| 发票进度 | `c4218f9` | 公共轮询调度器、客户端共享模板、旧 SSE 额度/寿命/释放、结果弹窗标题 ID 修正、新浏览器套件 |
| 旧部署入口 | `3f8f036` | HEALTH_URL 校验、active + HTTP 200、有界重试与非零退出；隔离部署与容量实验 |
| 可信代理 | `9f90363` | 标准库来源解析、启动配置校验、限流/审计共用、默认关闭及文档 |
| 验证与治理 | 本文所在提交 | 时间语义、诊断、依赖清单与查询、恢复/轮换/留存/告警准备资料及整体验收 |

查看真实差异：`git diff 72643c1..HEAD --stat`，逐批 `git show <提交>`。

## 先反例后修复

- 轮询模块不存在时 Node 专项失败；实现后 5 项通过，JS 全量 140 项通过。
- 新浏览器场景发现结果弹窗脚本使用 `invoiceJobResultTitle`，模板实际为 `invoiceJobModalTitle`，修正后 Chromium/WebKit 通过。
- 客户端模板旧断言要求 EventSource 的替代类，改为验证同一公共轮询脚本与资源存在；7 项通过。
- 旧部署两条测试包含多个场景，首次 6 个断言失败；修复后 2 项通过。测试替换 sudo/rsync/安装/服务/curl，未执行部署。
- 可信代理首次反例中，来源 A 达到阈值后来源 B 被同一代理地址连带拒绝（429 != 200）；实现后代理与现有限流共 12 项通过。
- 首版默认忽略头的测试误把哈希键当明文 IP，已修正测试为比较来源键，不改变产品以迎合断言。

## 实际 SSE 容量实验

真实 Gunicorn 26.2.0、1 worker / 8 threads，合成账号、真实 SSE 路由、后台任务不执行。

| 并存连接尝试 | SSE 状态 | 登录页 | 普通任务 API |
|---|---|---|---|
| 0 | 无 | 16.3 ms | 2.9 ms |
| 4 | 4 × 200 | 6.0 ms | 2.9 ms |
| 8 | 4 × 200 + 4 × 429 | 4.7 ms | 2.5 ms |

另发 20 个并发轮询读取，最慢约 37.6 ms；关闭连接后的额度恢复约 10.0 秒。
本次没有原有 2 秒超时。不是生产 SLA，也不是跨进程额度保证。
单元验证覆盖每成员额度、进程额度、不消费即关闭、重复释放、终态/授权失效、30 秒寿命及异常释放。

## 回归汇总

Windows Python 3.11 首次完整回归 **679 项，8 跳过，含分支统计的总覆盖率 80%**；JavaScript **140 项**。
Linux Python 3.13 首次完整回归 **657 项，1 failure / 2 errors**：资产两处、协作 setUpClass 一处会话失效；
协作整类初始化失败导致其余用例未执行，不能把 657 当完整通过数。

| 验证 | Windows | Linux / WSL |
|---|---|---|
| 完整 Python | 3.11：679 项、8 跳过；80%，超过 65% 门槛 | 3.13.16：首次失败如上；增加诊断后的第二次 679 项通过、80%；偶发根因仍未解决 |
| JavaScript | Node 22：140 项通过 | 本轮完整 JS 结果以 Windows 为准 |
| 浏览器 | Playwright 1.57.0：23 组全部通过，含现有 Chromium/WebKit 场景 | 软件渲染：local_collaboration、account_avatar、client_updates_avatar、invoice_polling 四组通过；未重跑其余 19 组 |
| 客户端页面 | 模板 7 项、桥接 11 项、打包页面 31 项通过；27 模板/291 资源构建，无页面错误或意外远程页面请求 | 89 项 Java 传输断言通过；不是 Android APK/真机运行验收 |
| 原生平台 | 未重新构建/运行 Tauri 安装包 | 未执行 Android 系统 WebView、Linux 桌面、相机、扫码枪、打印机现场验收 |

- Python 162 个源码文件编译检查及 Ruff 通过。
- 精确 `72643c1` 基准的旧库升级 1 项通过；合成恢复复验 11 个文件哈希一致、主库/资金库完整性均正常，头像、附件、资金回执和发票归属引用存在。
- 10 个模拟部署情形完成；HTTP 500/断连/超时/服务不活动各 30 次后退出 1，rsync 失败退出 23，重启或非法地址退出 1；均无完成提示。慢启动第三次成功。

## 偶发问题与环境失败记录

- 首次 Linux 失败和上轮客户端权限 401 属同类认证失效表象，尚不能断定相同根因。
  客户端模块独立、反向、固定随机（seed 20261025）各 14 项通过；资产/协作首次失败模块独立 57 项通过。
  没有以这些重跑结果抵消完整顺序失败。
- 第二次 Linux 完整测试带 `XTYF_TEST_AUTH_TRACE=1`，679 项通过且未记录签名拒绝；缺少失败现场，不能归因于时钟或共享状态。
- WSL 四组补查增加仅记录资源路径、失败类型、DOM 就绪/崩溃事件的诊断；未复现加载超时。日志中的导航取消不等同于加载失败，不能由软件渲染通过推断 GPU 是根因。
- `XTYF_TEST_AUTH_TRACE=1` 仅对合成夹具增加账号 ID、数据库/会话版本、状态以及签名拒绝类型/时间诊断；不输出 Cookie、密码或 Token。
- Windows 新增诊断早期有 SQLite 连接未显式关闭，25 项断言通过但退出清理出现 WinError 32；改用 closing 后完整回归无该清理错误。
- 跨 Windows/WSL 的 Git 指针与命令替换曾读取错误工作区，旧代码 SSE 测量无效；改为显式 Git 可见源码清单同步后才得到上表有效结果。
  升级检查首次因跨 shell 提前解析 SHA 得到空值而失败；改为在隔离 Linux 脚本内解析并校验完整 SHA，保留首次日志。
- 第二次 Linux Python 测试与覆盖率命令完成后，外层临时执行脚本因运行中扩充模式而报 `syntax error near unexpected token ')'`，包装命令退出 1；该错误不是测试失败。停止改写运行中脚本，当前脚本 `bash -n` 通过；原始 Python/覆盖率日志保留，未把包装命令计为成功。
- Windows Java 测试默认环境无 JDK，报 `A JDK is required; provide --jdk`；改用既有 Linux JDK 后 89 项通过。沙箱读取 WSL 日志曾报 `Wsl/E_ACCESSDENIED`，通过同一只读命令的服务权限重试后取得日志。
- 自动审批曾阻止新增代理测试清空登录计数；核实 test_tasks 先创建 TemporaryDirectory，增加数据库路径必须等于该合成夹具的断言后才执行。
- 故障注入产生的数据库/文件错误日志是预期反例；不与正常路径失败混为一谈。

## 依赖查询与分级处置

`tests/reliability_dependencies.py` 只读生成清单并调用 OSV，不安装/升级依赖。
本机没有 pip-audit 或 cargo-audit；使用标准库读取现有清单、锁文件和验证 Python 环境，再查询公开 OSV API。
扫描 571 条来源记录（PyPI 42、npm 17、crates.io 512），去重 564 个包版本均完成查询。
Python 环境清单不是生产锁文件；系统 WebView、操作系统/JDK、Git/path 依赖与手工 vendor 代码仍需单独核查。
本轮查询无 Python/npm 命中不代表整体无漏洞。

| 包/版本 | 公告与分类 | 当前证据及建议 |
|---|---|---|
| rustls-webpki 0.103.15 | [RUSTSEC-2026-0382](https://osv.dev/vulnerability/RUSTSEC-2026-0382)，证书信任 | reqwest 使用 rustls；优先安排独立依赖修复与 TLS 回归，公告修复版本 0.103.16 |
| webpki-roots 1.0.9 | [RUSTSEC-2026-0381](https://osv.dev/vulnerability/RUSTSEC-2026-0381)，信任根 | 同上，公告修复版本 1.0.10；未进行真实恶意证书利用验证 |
| glib 0.18.5 | [RUSTSEC-2024-0429](https://osv.dev/vulnerability/RUSTSEC-2024-0429)，VariantStrIter 内存安全 | Linux GUI 依赖链；需确认实际调用可达性和上游兼容升级，不能仅靠锁文件命中断言可利用 |
| zip 0.6.6 | [0367](https://osv.dev/vulnerability/RUSTSEC-2026-0367)、[0368](https://osv.dev/vulnerability/RUSTSEC-2026-0368)、[0369](https://osv.dev/vulnerability/RUSTSEC-2026-0369)，解压权限/链接 | updates.rs 手工 by_index 读取、拒绝链接并 create_new 写入，不调用公告所指 ZipArchive::extract，也不恢复 Unix 权限；尚未完成库全路径可达性验证。公告涉及大版本升级，另批评估 |
| proc-macro-error 1.0.4 | [RUSTSEC-2024-0370](https://osv.dev/vulnerability/RUSTSEC-2024-0370) | 停止维护提示，不等同于已确认漏洞 |
| zbus_names 4.3.5 | [RUSTSEC-2026-0371](https://osv.dev/vulnerability/RUSTSEC-2026-0371) | 停止维护，跟踪 Linux 依赖链 |
| zvariant 5.15.1 / zvariant_derive 5.15.1 | [0370](https://osv.dev/vulnerability/RUSTSEC-2026-0370)、[0372](https://osv.dev/vulnerability/RUSTSEC-2026-0372) | 停止维护，安排上游兼容性评估 |

8 个包命中 11 个公告标识，其中 glib 的 GHSA/RUSTSEC 是同一问题的别名。没有自动更新锁文件。
完整公开依赖清单及响应保存在本地 `.build-tools/audit-runtime/remaining-dependencies.json`（位于 E 盘原项目），可用脚本重新生成。

## 验证命令与边界

```text
python -m unittest discover -s tests -v
python -m coverage run --branch --source=app,common,modules,deploy -m unittest discover -s tests -v
python -m coverage report --fail-under=65
ruff check app.py common.py modules deploy tests
node --test tests/js/*.test.cjs
XTYF_UPGRADE_BASE_SHA=<72643c1 的完整 SHA> python tests/upgrade_from_main.py
python tests/reliability_experiments.py --output <隔离结果路径>
python tests/reliability_dependencies.py --scan --output <清单路径>
python clients/frontend/test_frontend.py
python clients/build_frontend.py
node clients/frontend/test_bridge.cjs
python clients/frontend/test_browser.py
python clients/android/test_transport.py
```

准确升级基准为本轮批准起点 72643c1，不冒充未来 PR 的 base.sha。
浏览器固定 Node 22 / Playwright 1.57.0，原有 22 组加 invoice_polling 共 23 组，diagnostics 不计套件。
WSL 的软件渲染变量仅对验证进程设置，不改变产品、Playwright 版本或断言/超时。

## 回滚与后续

按依赖逆序 `git revert <本文所在提交>`、`git revert 9f90363`、`git revert 3f8f036`、`git revert c4218f9`。
没有删除业务数据的逆向迁移。代理代码回滚前先撤销相应配置；撤销部署检查恢复误报风险；撤销 SSE 保护恢复线程耗尽风险。
保留 D1–D4；资金幂等语义不回退。

治理准备见 [恢复与治理](../operations/recovery-and-governance.md)。RPO/RTO、异地位置、密钥轮换、数据留存及告警责任人仍需确认。
生产启用、客户端页面发布、真实相机/扫码枪/打印机及原生平台运行属于后续独立交付。
