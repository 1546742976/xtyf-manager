# 本地界面客户端：Tauri 桌面端与 Android

Windows、Ubuntu、Fedora 使用 Tauri 2，Android 使用系统 WebView。登录页、全部业务页面、脚本、样式、字体和 PDF 阅读器随安装包内置。支持更新协议的客户端还可从配置服务器下载经过发布者签名验证的完整页面资源，在本地加载；兼容服务器优先使用本地界面；服务器缺少客户端接口时，可在应用内独立网站视图继续使用网站。

默认服务器为 **http://manager.skywalker-rm.online/**。断网可以打开本地界面和服务器设置，业务操作需要联网。站外 HTTP/HTTPS 链接在 App 内独立浏览视图打开，具有返回、关闭入口，不调用系统浏览器。图片及 PDF 在 App 内预览，其余附件通过系统保存窗口下载。

## 安装和升级

- Windows 10/11 x64：`dist/clients/windows/XTYF-Manager-Setup-<版本>-x64.exe`。NSIS 安装器提供快捷方式和卸载入口；缺少 WebView2 时联网补齐运行时。EXE 未使用发布者证书签名。
- Android 8.0+：`dist/clients/android/XTYF-Manager-<版本>.apk`。保留包名 `online.skywalker.manager` 和既有发布密钥，递增 versionCode 后覆盖安装。返回使用系统返回键或手势。
- Ubuntu 22.04 ARM64：`sudo apt install ./XTYF-Manager-<版本>-arm64.deb`。
- Fedora 44/45 x86_64：`sudo dnf install ./XTYF-Manager-<版本>-x86_64.rpm`。

桌面升级保留服务器地址，首次从 Electron 迁移需要重新登录。Windows 安装器识别旧版 Electron 的卸载记录，保留用户数据后完成替换；Linux 保持 `xtyf-manager` 包名。Android 保留原服务器地址设置，新的原生会话首次需要登录。

服务器地址支持 HTTP/HTTPS 根地址及自定义端口，按输入的协议连接，HTTPS 正常校验证书。保存时保留输入协议；连接探测允许同主机从 HTTP 80 升级到 HTTPS 443，或保持同一自定义端口的 HTTPS 升级。实际连接地址会显示在设置中，不覆盖输入地址。不接受跨主机、非预期端口或 HTTPS 降级，也不自动重放提交请求。升级保留已保存的服务器地址。切换服务器会取消当前请求、清空页面状态，各服务器会话分别保存。

## 服务器兼容性

客户端需要协议版本 **1**：

- `GET /api/client/bootstrap`：协议版本、登录状态、公开用户信息、权限、主题和业务常量。
- `POST /api/client/login`、`POST /api/client/logout`：JSON 认证接口，复用原有账号、Cookie、CSRF、限流和审计。
- 其余功能继续调用既有业务 API。

支持 `website_fallback` 的客户端在冷启动遇到 bootstrap 404/405、成功返回 HTML 或不兼容协议时，自动在客户端内打开网站。超时、证书错误及 5xx 保留本地界面、重试、设置和手动打开网站入口；使用期间不会自动切换或重载，手动切换会提示未保存内容风险。旧客户端缺少此原生能力时仍显示原来的服务器更新提示。

网站模式占满内容区域，桌面端和 Android 均不显示客户端工具栏、网址提示或菜单按钮，也不提供隐藏菜单、快捷键或手势入口。网站自身的侧栏、账户设置和主题选择保持原样；独立外链窗口仍保留返回／关闭操作。桌面进入网站模式后没有客户端设置或返回本地界面的操作入口，重新启动仍可能自动进入网站模式。Android 保留系统返回：有网页历史时后退，到历史根处确认未保存内容风险后返回本地界面；返回后不会循环自动打开网站。

网站登录 Cookie 持久化，与原生接口登录分开，首次进入可能需要重新登录。网站视图没有本地通用原生命令权限，普通选图、上传和下载使用网站自己的会话。桌面文件下载完成后使用系统保存窗口，下载或保存失败通过原生错误提示反馈；成功、取消保存或切换服务器不额外弹窗。网站功能以云端实际发布的版本为准，本地头像功能不会给尚未部署该功能的网站增加接口。

**构建客户端不会部署服务器。** 服务器上线必须包含本次接口源码；服务器源码包仍通过 `python deploy/package_release.py` 从 Git HEAD 独立生成，未提交的代码不会自动进入该源码包。线上兼容性必须在服务器实际更新后验证。

## 统一版本交付

### 页面资源发布（不生成安装包）

页面构建标识独立于客户端版本；纯页面发布不递增 `clients/config.json`。
服务器发布入口 `python deploy/package_release.py` 从同一个 Git HEAD 构建网站源码及配套页面，校验后将签名资源放入源码包内的 `client-ui-release/`，随服务端一起上线。该命令只生成本地交付文件，不部署服务器。

- 签名公钥为受版本控制的 `clients/update-public-key.pem`。独立私钥保存在被忽略的 `clients/signing/ui-update-private.pem`；需单独离线备份，不能放进源码包、安装包或服务器公开目录。
- 默认读取该私钥，也可使用 `--ui-signing-key <path>` 或 `XTYF_UI_SIGNING_KEY`。密钥缺失或与公钥不匹配时发布失败，不生成无签名更新。
- 发布序号在私钥旁的 `.sequence` 文件中单调递增。回退页面也须重新发布，使用更高序号指向旧页面内容；不要恢复较旧的序号文件。并行发布锁失败时先检查另一发布进程，不自动删除锁。
- 默认没有安装包下载入口。已有正式发布产物时，通过 `--installers <reviewed.json>` 提供 `windows-x86_64`、`ubuntu-aarch64`、`fedora-x86_64`、`android` 的 `{version,url,sha256}`。先核对正式构建报告、SHA256SUMS 和实际上传文件；URL 必须是当前服务器内的路径，不能填写待构建产物。
- 更新接口 `/api/client/updates` 和 `/api/client/ui/<id>.zip` 仅公开签名清单与公共页面，不含账户、数据库、头像、附件或配置。没有发布资源时客户端继续使用原界面。

完整数据格式、资源限额和原生操作见 [页面更新协议](update-protocol.md)。旧客户端必须先安装一次支持此协议的版本。发布资源不能给旧壳增加新原生能力；能力不兼容时提示升级。页面更新与系统安装包更新是两条独立流程。

`clients/ui_release.py` 是发布流程内部使用的资源构建器，也可用于隔离测试。直接运行它会读取当前工作目录的源码，`--source-version` 只是调用者提供的标识；正式交付应使用上面的 `deploy/package_release.py`，由 Git HEAD 决定源码与页面来源。

新版客户端在启动、前台每 30 分钟，以及距离上次检查超过 5 分钟重新回到前台时检查更新。下载完成后当前页面继续使用原版本，下次启动启用新版。服务器设置中的“立即重载”会先提示未保存内容可能丢失；也可手动检查或重试失败版本。启动失败自动回退，断网仍可进入本地页面和设置。切换服务器时，各服务器的缓存和更新记录相互隔离。

新桥接协议支持从已选择的文件句柄读取图片预览，因此客户端可使用账户页的头像裁剪、保存和恢复默认功能。旧客户端没有 `image_preview` 能力时仍隐藏编辑入口。安装包下载只保存已验签清单中当前平台的文件，并检查 SHA-256；完成系统安装仍需用户操作。

### 安装包交付

生成安装包前按 [项目交付约定](../AGENTS.md) 询问是否生成及目标版本号。先完成代码修改与必要验证，在项目根目录预览发布入口预计生成的版本：

```powershell
python clients/release.py --dry-run
```

获得用户对生成安装包和版本号的明确确认，且预计版本与确认一致后，再执行：

```powershell
python clients/release.py
```

当前脚本默认递增一次补丁版本和 Android versionCode；用户确认其他新版本时，预览和正式构建均添加 `--version X.Y.Z`。指定的新版本必须高于已发布版本，Android versionCode 仍只递增一次。先检查构建环境，再构建一次共享前端，四个平台复用同一份资源清单。四个安装包全部构建并检查通过后才替换报告、发布新版和清理旧包；失败恢复版本及原报告，保留旧安装包。

用户明确要求保持当前版本重新生成时，使用显式同版本重建入口；例如当前已发布版本为 1.0.11：

```powershell
python clients/release.py --version 1.0.11 --rebuild-current --dry-run
python clients/release.py --version 1.0.11 --rebuild-current
```

该入口要求配置、已有发布报告及各平台版本元数据一致，不修改版本号、Android versionCode 或版本文件内容。四包完整校验后，旧安装包、报告、校验和及版本元数据快照保留在 `dist/clients/.rebuild-backups/`，随后逐个原子替换交付物；替换失败恢复原交付物并保留备份。新报告记录 `releaseMode`、源码提交及是否存在已跟踪未提交修改、`previousReleaseBackup`，用于区分相同版本的不同构建。备份目录由报告指明，不随旧安装包清理而删除。

同版本重建不会触发按版本号检测的升级，需要手动安装对应安装包。沿用原有签名及用户数据，实际覆盖安装与运行结果须另外记录。

版本来源是 `clients/config.json`，发布入口同步 Tauri 配置、Cargo 和 Android Manifest。不要手工修改版本进行正式交付。

交付记录：

- `dist/clients/build-report.json`：版本、架构、包检查、构建环境及实际运行验证范围。
- `dist/clients/SHA256SUMS.txt`：四个安装包的 SHA-256。
- `clients/frontend/dist/asset-manifest.json`：前端协议、页面和资源哈希；同一次发布的四包使用同一清单。

只读检查、纯文档整理或用户明确要求不打包时无需发布。不推送 GitHub、不部署服务器、不设置后台监听或定时打包。

## 本地构建环境

Python 3.9+（前端构建需要 Jinja2；首次还原第三方资源需访问 npm registry）、Node.js/npm、Visual Studio C++ Build Tools、Rust 1.99.1，以及既有 JDK 17+ / Android platform 36 / build-tools 36。Python 业务回归使用项目 `.venv`。

Windows Rust 安装在项目 `.build-tools/cargo` 与 `.build-tools/rustup`，构建输出位于 `.build-tools/tauri-target-windows`。Tauri CLI 由 `clients/desktop/package-lock.json` 锁定，Rust 依赖由 Cargo.lock 锁定。

```powershell
# 设置工具目录后安装固定 Rust 版本，不修改系统 PATH
$env:CARGO_HOME = Join-Path $PWD '.build-tools/cargo'
$env:RUSTUP_HOME = Join-Path $PWD '.build-tools/rustup'
# 首次通过官方 rustup-init.exe 安装；已有 rustup 时：
& "$env:CARGO_HOME/bin/rustup.exe" toolchain install 1.99.1 --profile minimal

# 安装 Windows Tauri CLI
npm.cmd ci --prefix clients/desktop --no-audit --no-fund
python clients/verify_windows.py --setup

# 准备本地前端（供应商资源固定版本及哈希；首次从锁定 npm 源下载，后续走本地缓存）
python clients/build_frontend.py

# Linux 首次准备：Ubuntu 22.04 WSL 中安装主机开发库和独立 ARM64 sysroot
python clients/build_linux.py --setup
python clients/build_linux.py --check
```

Linux 默认使用 `Ubuntu-22.04` WSL 的 root 构建用户，缓存位于 `/root/.cache/xtyf-manager-build`；可通过脚本参数指定发行版和用户。ARM64 开发包提取到独立 sysroot，不修改主机 apt 软件源或外国架构列表。Linux 依赖及输出与 Windows 分开。

单平台生成安装包同样需要确认是否生成及版本号；以下命令只用于按当前版本调试，不代替四包正式发布（`-Check` 仅检查环境）：

```powershell
.\clients\build_windows.ps1 -Check
.\clients\build_windows.ps1 -OutputDirectory .build-tools/windows-debug
python clients/build_android.py --output-dir .build-tools/android-debug
python clients/build_linux.py --output-dir .build-tools/linux-debug
```

正式发布只构建一次共享前端，Android 使用 `--skip-frontend-build` 复用资源。Android 仍由 aapt2、javac、D8、zipalign、apksigner 构建，不依赖 Android Studio。

请独立备份整个 `clients/signing/`，后续覆盖安装需要原密钥和密码文件。密钥、用户数据、数据库、附件和邮件配置不进入客户端安装包。


## GitHub Actions 测试安装包（PR／main 测试验收）

现有 [Client validation](../.github/workflows/clients.yml) 仅在 `clients/**`（排除 Markdown 文档）、`templates/*.html` 或 `static/**` 变化时自动触发，覆盖指向 `main` 的 PR 和 `main` 推送。在原有前端／Rust／Java 验证通过后，使用 Windows x64／ARM64、Ubuntu 22.04 x64／ARM64 和 Android SDK runner 构建实际客户端。打开 GitHub Actions → **Client validation** → 对应 PR 或 main 的运行 → **Artifacts**，获取以下产物：

- `xtyf-client-windows-x64` 和 `xtyf-client-windows-arm64`：两个独立架构的 NSIS `.exe`、安装包验证报告与 SHA-256。
- `xtyf-client-linux-x64` 和 `xtyf-client-linux-arm64`：分别内含对应架构的 Ubuntu 22.04 `.deb`、Fedora `.rpm`、验证报告与 SHA-256。
- `xtyf-client-android-apk`：一个通用 `.apk` 与 SHA-256。Android 原生层为纯 Java/WebView，没有 `.so` ABI 库，因此该 APK 同时支持 ARM64 和 x86_64，无需生成重复 APK。

产物保留 14 天。CI 只使用 `clients/config.json` 的当前版本，不调用会递增版本的 `clients/release.py`，不部署服务器、不上传任何密钥。PR 只上传 Artifacts；客户端变更合并或直接推送到 main 后，全部构建通过才发布测试 Pre-release。Windows 未进行代码签名；Android 每次运行临时生成不同的测试签名密钥，**不能覆盖安装正式签名 APK，也不保证两次 CI 生成的 APK 可以相互覆盖升级**。这些测试安装包不属于正式交付，不应分发给生产用户。Linux 构建进行实际包结构、ELF 架构与依赖校验，但不能替代 Ubuntu ARM64 和 Fedora 的真机运行验收。

## 验证边界

- 资产单件页面通过 bootstrap 的 `asset_units_enabled` 开关启用。扫码组件位于 `static/vendor/asset-tools`，锁定 @zxing/library 0.21.3 和 JsBarcode 3.12.1 的版本与 SHA-256；维护脚本为 `clients/vendor_asset_tools.py`，构建器原样复制第三方代码。
- Android 相机仅授予可信 HTTPS 网站或本地安全页面的视频采集权限，不授予音频；外域、异常端口、HTTP 请求拒绝。HTTP 网站模式使用相册或手动输入。需要新版 APK 才具有原生权限处理，本次只验证源码，不生成安装包。
- 单件／物料标签 PDF 经现有认证下载通道保存。手机相机、实体扫码枪和打印机必须另记实机结果，编译和浏览器模拟不能替代。
- 资产 PDF 预览使用本地固定的 PDF.js 5.4.624，经 `read_resource` 读取认证文件；`pdf_save` 能力表示支持通过既有 `download` 命令的可选 `pdfBody` 保存同一份 PDF 字节（上限 20 MiB）。原下载参数保持兼容。没有该能力的旧客户端仍可预览，并提示升级后保存或改用浏览器。本轮源码修改不等于已经生成含该能力的新安装包。

- 页面更新协议可用 `python -m unittest discover -s tests -p "test_client_u*.py"` 验证。跨语言测试先运行 `python tests/client_update_fixture.py .build-tools/client-update-fixture`，该命令仅使用临时签名密钥生成公共测试资源，不发布或生成安装包。
- Android 原生存储与 HTTP 生命周期使用 `python clients/android/test_ui_updates.py --fixture .build-tools/client-update-fixture`；需要现有 JDK、Android SDK 以及固定 SHA-256 的测试用 `org.json:json:20240303` JAR（默认在 `.build-tools/client-ui-updates/android-tests/`）。该检查执行 JVM 断言并编译 Activity，不生成 APK。
- 桌面原生检查为 `cargo test --manifest-path clients/desktop/Cargo.toml --locked`；本机回环 HTTP 测试应设置 `NO_PROXY=127.0.0.1,localhost`，避免系统代理介入隔离测试服务器。
- 业务会话由原生网络层管理，保留 CSRF、服务端权限及认证；网站和站外视图不共享原生接口 Cookie 或通用原生命令权限。
- 桌面端在 Unix 系统使用私有配置目录和会话文件，通过原子替换保存 Cookie。桌面端与 Android 的 API／附件预览响应最多读取 128 MiB，超过限额时返回错误；另存为下载仍使用流式处理。
- 安装包检查包含本地资源清单、版本、真实架构、Linux 动态依赖和桌面入口；Ubuntu ARM64 的 GLIBC 上限为 2.35。
- Windows、Android、Ubuntu ARM64、Fedora 的实际启动、导航、上传下载和业务操作分别记录。包检查、APK 签名与对齐通过不能替代目标系统运行验证。
- 交付记录中的运行验证仅在确实执行后标记通过。模拟器测试与真机测试分别说明；未部署服务器不等于线上业务已经验收。

参考：[Tauri 分发](https://v2.tauri.app/distribute/)、[Tauri Linux ARM64 构建](https://v2.tauri.app/distribute/debian/)、[Android 本地内容](https://developer.android.com/develop/ui/views/layout/webapps/load-local-content)。

## 应用图标源文件

原始彩色图标以透明 SVG 形式保存在 `clients/assets/xtyf-logo.svg`。旧 JPEG 转为白色方形图标时带来的上下白边已移除。图标只保留透明安全间距；桌面 PNG/ICO 与 Android 512px 启动图标属于 **构建输出**，由 `python clients/build_icons.py` 调用固定版本 Tauri CLI 生成，不进入 Git。首次运行前安装依赖：`npm ci --prefix clients/desktop`。所有 Actions 打包任务都会重新生成同一份图标；不会拿旧二进制图标冒充修复版本。


## GitHub Actions 自动发布

客户端代码或打入安装包的共享 UI 资源变更合并／直接推送到 `main`，全部客户端构建通过后自动发布 `YYYYMMDD-betaN` 测试 Pre-release；PR 阶段只编译并上传 Artifacts。推送 `vX.Y.Z` 标签后自动发布正式 Release（须预先配置原 Android 签名密钥 Secrets）。发布前严格核对所有平台安装包 SHA-256 和版本，缺一不发布。详情与密钥配置步骤见 [Actions 版本发布指南](../.github/RELEASING.md)。
