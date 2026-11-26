# 资产逐件管理与包装码登记交付记录

日期：2026-10-21。开发基准：`upstream/main c3a20f75f4804acec57087abe5b972cf51cf691b`，包含 PR #45。独立分支：`codex/assets-serialized`。

本次工作目录为原项目下的 `.build-tools/assets-serialized` Git worktree。原 `assets` 工作区未切换、未迁移业务库。没有推送、部署、安装包或版本修改；现有 `1.1.0 / Android versionCode 14` 保持不变。真实图片、采购订单、库存、数据库和密钥未加入提交。

## 分批差异与回滚

| 批次 | 本地提交 | 实际差异摘要 | 行为变化 |
|---|---|---|---|
| 1 | `f5e8a82` | 7 文件，+309 / -4 | 新增只读包装解析、供应商映射、实收确认、来源记录、重复核对和并发幂等 |
| 2 | `3da63f2` | 11 文件，+574 / -19 | 新非耗材逐件建档、型号抽屉、批量借还、责任确认、旧库存人工迁移；旧数量接口不能绕过 |
| 3 | `3a2707f` | 26 文件，+868 / -140 | 本地固定版本二维码／条码组件、相机／相册／键盘入口、Android 来源权限、A4／热敏 PDF |
| 4 | `40cc5ec` | 22 文件，+1181 / -320 | 使用对象、整车移动、装拆、维修、处置恢复、冻结盘点、完整历史翻页、来源展开及综合验收 |
| 验证修正 | `1ccbbba` | 1 文件，+2 / -1 | rsync 测试使用相对路径，避免 Windows 盘符被误判为远程主机；断言保留 |

后续批次含对前批功能的联合校验修正和格式整理。实际 diff 使用 Git 查看，不以摘要替代：

批次 1 对应 `test_asset_scan.py`（5 项），批次 2 对应 `test_asset_units.py`（6 项），批次 3 对应 `asset_codes.test.cjs`（3 项）、两类 PDF、客户端页面与 Android 检查，批次 4 对应 `test_asset_lifecycle.py`（6 项）及 `assets_serialized.cjs`。这些均在最终资产 43 项／浏览器与客户端复验中通过；原有 `test_assets.py` 26 项同时保留。可将下文 `test_asset*.py` 替换为具体文件名逐批复验。

```powershell
git show --format=fuller f5e8a82
git show --format=fuller 3da63f2
git show --format=fuller 3a2707f
git show --format=fuller 40cc5ec
git show --format=fuller 1ccbbba
git diff c3a20f7..1ccbbba
```

本地 `.build-tools/serialized-acceptance/patches/` 保存以上各提交补丁及 `all-code.patch`，由 Git 原样导出，未放入源码。

没有业务写入时，代码可按依赖逆序回滚：

```powershell
git revert 1ccbbba
git revert 40cc5ec
git revert 3a2707f
git revert 3da63f2
git revert f5e8a82
```

上述命令不是数据库回退脚本。已产生单件数据时禁止直接退回旧数量写入逻辑继续营业；先停写，保留数据库、附件和流水，采用兼容修复。恢复数据快照需列出快照后差异并另行确认。未编写删除新表或清空身份的回滚迁移。

## 数据和依赖指标

- 原资产相关测试基线 26 项，本次最终资产回归 43 项，全部通过。
- 新增 10 张关联表，以及物料 `tracking` 增量字段；原成员、库存、借单、流水主键不替换。旧非耗材默认 `legacy`，人工冻结、逐借单核对后才能成为 `unit`。
- 库存列表以固定批量汇总取得单件数量，原数量台账与迁移后单件不会重复累计；来源记录展示使用批量查询，不逐流水取来源。
- 新增本地解码／标签脚本 363,842 字节（ZXing 336,008 + Code128 27,834），另带许可证和内容锁。运行时不从 CDN 下载。
- 后端 PDF 新增 `reportlab>=4.4,<5`，本次实际验证版本 4.5.1。其余 Flask、数据库、Tauri 2 和 Android WebView 技术栈沿用。
- 迁移测试旧库有在库 4 件、未还 2 件；升级并重启后数量原样保留、仍为 legacy、单件表为空。重复迁移及 SQLite 备份恢复保留身份、未还责任和不可删除流水。

## 实际验证

测试使用 Windows 本机 Python 3.13、Node 22、Playwright 1.57.0；所有库存为合成数据，邮件使用隔离测试／模拟发送。

| 验证 | 实际结果 |
|---|---|
| Python 编译、Ruff | 通过 |
| 完整 Python 回归 | 502 项：496 通过，6 项现有平台条件跳过，0 失败 |
| 覆盖率（含分支） | 77%，通过现有 65% 门槛 |
| 最终资产针对性回归 | 43/43；完整回归后补验扫码标签名称及长名称 PDF 排版 |
| JavaScript 全部单测 | 120/120，0 跳过 |
| 客户端桥接单测 | 11/11 |
| 客户端页面构建 | 6/6，24 模板、282 静态资产 |
| 精确基准旧库升级 | 1/1，实际 SHA 为 c3a20f75f4804acec57087abe5b972cf51cf691b |
| 原资产浏览器场景 | Chromium、WebKit 各含网页及本地桥接模拟，共 6 组通过 |
| 新逐件浏览器场景 | Chromium、WebKit 通过：抽屉、批量借用、响应丢失重试、相机不可用／拒绝降级、相册 QR、HID 重复、实收差异、两类 PDF、对象编辑、盘点结束及手机布局 |
| Android HTTP／来源规则 | JVM 85 条断言通过，含 HTTPS／跨域／端口／HTTP 相机规则 |
| Android 更新及 Activity | JVM 84 条断言通过；Activity、网络层、更新层对 API 36 编译成功，无 APK |

完整回归第一次遇到既有 rsync Windows 路径问题，修复后重新运行，得到上表结果。6 项跳过为 5 项 Windows 当前环境无法创建符号链接的测试及 1 项依赖 POSIX flock 子进程的测试；未新增跳过、放宽断言或增加超时。运行日志中的合成坏包错误和资源警告不等于测试失败。

当前 main 的业务浏览器 CI 原来固定 Playwright 1.56.1，本次只追加新套件，未顺带改全项目引擎版本。本地上述浏览器结果来自 1.57.0，不能代替后续远程 CI 实际结果。没有声称已运行 Linux、Android 真机或 Windows 原生 WebView2 验收。

## 复验命令

以下在本 worktree 根目录执行，使用项目测试环境的 Python / Node。JDK、Android SDK、JSON 测试 JAR 路径按本机实际配置填写。

```powershell
python -m compileall -q app.py common.py modules tests deploy
ruff check app.py common.py modules deploy tests
python -m unittest discover -s tests -p 'test_asset*.py' -v
python -m coverage run --branch --source=app,common,modules,deploy -m unittest discover -s tests -v
python -m coverage report --fail-under=65
node --test tests/js/*.test.cjs
node --test clients/frontend/test_bridge.cjs
python -m unittest discover -s clients/frontend -p test_frontend.py -v

# .ci-main-baseline 必须是准确的 PR 基准，脚本核对 HEAD，不能用任意旧 main。
$env:XTYF_UPGRADE_BASE_SHA='c3a20f75f4804acec57087abe5b972cf51cf691b'
python tests/upgrade_from_main.py

# NODE_PATH 指向本次固定的 Playwright 1.57.0；REVIEW_PYTHON 指向测试 Python。
node tests/browser/assets.cjs
node tests/browser/assets_serialized.cjs
python clients/android/test_transport.py --jdk <JDK路径>
python clients/android/test_ui_updates.py --jdk <JDK路径> --json-jar <JSON测试JAR> --android-jar <Android-36-JAR>
```

本地证据在 `.build-tools/serialized-acceptance/`，浏览器结果与截图在 `.build-tools/assets-check/` 及 `.build-tools/assets-serialized-check/`。这些目录被 Git 忽略。

## 尚待独立验收

- 国内嘉立创商品自动查询：未配置、未接入；取得官方权限与文档后另批实现。没有采用参考项目的 PHP／MySQL，也没有将第三方抓取接口冒充官方服务。
- Android 真机相机授权、拒绝后重试、前后台停止和服务器切换；HTTP 网站模式相册输入。
- 实体 HID 扫码枪的 Enter／Tab 配置和输入法焦点；浏览器测试使用模拟键盘输入。
- A4／热敏打印机的实际尺寸、可读性、重印身份；本次验证 PDF 和解码算法，不宣称实体打印通过。
- 正式部署前备份实际业务数据库。旧非耗材的实物核对和借单对应须由管理人员在真实现场完成，不由测试或迁移脚本猜配。

操作说明见 [资产管理](../usage/资产管理.md)。
