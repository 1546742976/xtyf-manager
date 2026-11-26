# 资产模块五项审查修复（2026-10-22）

本轮基于 `codex/assets-serialized-ui` 的 `2f820dd`，沿用现有 Flask、数据库及客户端技术栈。没有数据库迁移，没有纠正历史库存；所有写入验证均使用临时数据库和合成数据。未推送、合并、部署、递增版本或生成安装包。

## 修复与证据

| 问题 | 修复后行为 | 验证 |
| --- | --- | --- |
| 服务端成功而响应损坏，重试重复入库 | 只有结构明确的 4xx 业务拒绝或确认成功才清除待确认请求；其余保留原正文和 request_id，刷新后可继续确认 | HTML、截断 JSON、缺字段对象、null、503：两次异常及刷新后第三次重试，三次请求正文相同，库存始终为 10，只有一笔入库流水；修复前为 20、两笔流水 |
| 原校区看不到单件移出流水 | 事件主校区、数量 deltas、单件 before/after 校区取 OR；不改历史数据 | 移动、异地归还、整车移动；超过一页时总数一致且事件 ID 不重复 |
| 相机失败误报成功、同码无法重试 | 解析返回 success/failure/stale；失败保留原文与清单，显示“重试此标签”；相同连续帧不反复请求，停止相机和关闭弹窗均使迟到响应失效 | 失败振动次数 0；重复帧只请求一次；显式重试后共请求两次并成功；关闭和停止后的迟到结果不更新界面 |
| 标签画布与实际 PDF 不同 | 直接渲染已认证读取的 PDF，并保存同一份字节；修改尺寸清空旧预览，旧响应不能覆盖新请求 | 25 张标签的 13 页 A4、40×270 和 100×30 mm 热敏纸；比例、页数、中文、身份编号、浏览器实际下载字节一致；失败后无旧画布或下载入口 |
| 我的借用顶部与单件列表矛盾 | 显示全部校区的数量借单数、未还单件数、待接收件数；数量借单与单件分组命名独立 | 无借用、只有数量、只有单件、混合、移交待接收、归还后归零；22 件单件跨页和校区筛选仍汇总 22 件 |

## 接口和客户端兼容

- 写接口、数据库结构、原 PDF 下载接口保持不变。
- `/api/assets/units?mine=1` 增量返回 `summary.quantity_loans`、`summary.unit_loans`、`summary.pending_receipts`，均限定当前登录用户、统计全部校区，不受页码或校区筛选影响。
- `/vendor/pdfjs/<resource>` 仅允许 `clients/frontend/vendor-lock.json` 已列出的 PDF.js 文件，并检查解析后的路径包含关系；不开放客户端源码或任意目录。
- PDF.js 沿用项目固定的 **5.4.624**。字体、CMap、Worker、WASM 均走本地公共资源，不通过外部 CDN 或供应商服务。
- 原生 `download` 增量接受 `pdfBody`，仅接受大小不超过 20 MiB、带 PDF 文件头的 Base64 内容，仍由用户选择保存位置。`pdf_save` 能力表示支持该方式。旧客户端可预览；保存入口明确提示需要升级，避免重新生成的文件冒充当前预览。
- 客户端业务 iframe 的字体读取交给本地 Worker，避免被业务请求适配器转发到服务器。构建后的真实客户端页面已通过浏览器模拟 IPC 检查读取和保存链路；这不等同原生系统窗口运行验收。

PDF.js 缓存原本被 Git 忽略。`deploy/package_release.py` 现在从同一 HEAD 的前端构建结果中，只复制锁文件列出的 PDF.js 公共资产，并重新校验 SHA-256；未列出的文件不进入源码包。该逻辑以合成构建测试验证，本轮没有执行发布器生成实际交付包。

从干净源码运行网站前，可用 `python clients/frontend/vendor_dependencies.py` 恢复并校验固定公共依赖；构建机需要首次下载访问 npm，运行时不自动下载。正常源码发布流程会携带上述已校验资源，服务器运行时无需访问 npm。

## 验证命令与范围

在本 worktree 根目录运行，Python 为 3.13.13，Node 为 24.17.0，Playwright 为 1.57.0。Node 版本与 CI 的 22 不相同，本轮结果不代表远程 CI 已运行。

```powershell
python -m compileall -q app.py common.py modules tests deploy
python -m ruff check app.py common.py modules deploy tests
python -m unittest discover -s tests -p test_asset_event_campuses.py -v
python -m unittest discover -s tests -p test_asset_mine_summary.py -v
python -m unittest discover -s tests -p test_asset_pdf_resources.py -v
python -m unittest discover -s tests -p test_client_ui_publisher.py -v
python -m coverage run --branch --source=app,common,modules,deploy -m unittest discover -s tests -v
python -m coverage report --fail-under=65
node --test tests/js/*.test.cjs
node --test clients/frontend/test_bridge.cjs
python -m unittest discover -s clients/frontend -p test_frontend.py -v
$env:XTYF_UPGRADE_BASE_SHA='c3a20f75f4804acec57087abe5b972cf51cf691b'
python tests/upgrade_from_main.py
```

浏览器依次运行 `node tests/browser/<suite>.cjs`，保留现有 17 组：`review_fixes`、`pr22_merge`、`task_center`、`task_schedule`、`daily_mail`、`action_failures`、`login_mobile`、`palette_workspace`、`local_collaboration`、`account_avatar`、`account_invoice_ui`、`client_updates_avatar`、`client_connection`、`seven_repairs`、`seven_repairs_review`、`assets`、`assets_serialized`。新增针对性场景接入 `assets_serialized` 和 `client_updates_avatar`，没有取消原有断言或增加超时。

客户端验证命令：

```powershell
python clients/build_frontend.py
cargo test --manifest-path clients/desktop/Cargo.toml --offline
python clients/android/test_transport.py --jdk <本地JDK目录>
python clients/android/test_ui_updates.py --jdk <本地JDK目录> --json-jar <JSON测试JAR> --android-jar <Android-36-JAR>
```

前端构建仅生成公共页面资源，Rust 命令只编译测试程序；未调用安装包或版本发布入口。Rust 首次检查因 worktree 缺少生成的页面清单及图标而未通过，补齐本地生成资源后 34 项测试全部通过。Android JVM 89 条网络/保存断言、84 条更新断言通过，Activity 对 SDK 36 编译通过。

其他结果：JavaScript 123 项通过、桥接 11 项通过、前端构建测试 6 项通过、准确基准旧库升级 1 项通过；17 组浏览器全部通过，随后对修改到的逐件资产及真实构建客户端 PDF 场景单独复验通过。

最终 Python 完整回归 **512 项：506 通过、6 跳过**，耗时 523.496 秒；分支覆盖统计合计 **78%**，通过 65% 门槛。跳过为 5 项 Windows 环境符号链接测试及 1 项 POSIX flock 子进程测试，没有新增跳过。编译检查、Ruff 和 `git diff --check` 均通过。最终日志为 `python-final.log`、`coverage-final.log`。

日志和截图位于忽略目录 `.build-tools/assets-review-fixes/`；资产浏览器细项结果在 `.build-tools/assets-serialized-check/`，原生桥接页面细项在 `.build-tools/client-updates-avatar-check/`。真实手机相机、扫码枪、打印机、原生保存对话框和非 Windows 目标平台实际运行未验收。

## 本地批次与回滚

1. `e196afd`：数据安全与跨校区流水，5 个文件，99 行增加、5 行删除。
2. `bd0d768`：扫码失败重试与借用汇总，9 个文件，152 行增加、23 行删除。包含停止相机的迟到响应回归。
3. 本文所在提交：真实 PDF 预览、原生保存与整体验证；以交付消息中的提交号为准。

各批实际补丁可用 `git show --format=fuller --stat --patch <提交号>` 查看；本地证据目录另保留三份 `.diff`。回滚按第三批、第二批、第一批逆序执行 `git revert <提交号>`。代码回滚不会删除库存、流水、附件或其他业务数据，也不自动修正历史重复库存。
