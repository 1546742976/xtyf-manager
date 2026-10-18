# 本地界面客户端：Tauri 桌面端与 Android

Windows、Ubuntu、Fedora 使用 Tauri 2，Android 使用系统 WebView。登录页、全部业务页面、脚本、样式、字体和 PDF 阅读器随安装包内置。支持更新协议的客户端还可从配置服务器下载经过发布者签名验证的完整页面资源，在本地加载；主界面不直接加载服务器 HTML，也不使用在线 CDN。

默认服务器为 **http://manager.skywalker-rm.online/**。断网可以打开本地界面和服务器设置，业务操作需要联网。站外 HTTP/HTTPS 链接在 App 内独立浏览视图打开，具有返回、关闭入口，不调用系统浏览器。图片及 PDF 在 App 内预览，其余附件通过系统保存窗口下载。

## 安装和升级

- Windows 10/11 x64：`dist/clients/windows/XTYF-Manager-Setup-<版本>-x64.exe`。NSIS 安装器提供快捷方式和卸载入口；缺少 WebView2 时联网补齐运行时。EXE 未使用发布者证书签名。
- Android 8.0+：`dist/clients/android/XTYF-Manager-<版本>.apk`。保留包名 `online.skywalker.manager` 和既有发布密钥，递增 versionCode 后覆盖安装。返回使用系统返回键或手势。
- Ubuntu 22.04 ARM64：`sudo apt install ./XTYF-Manager-<版本>-arm64.deb`。
- Fedora 44/45 x86_64：`sudo dnf install ./XTYF-Manager-<版本>-x86_64.rpm`。

桌面升级保留服务器地址，首次从 Electron 迁移需要重新登录。Windows 安装器识别旧版 Electron 的卸载记录，保留用户数据后完成替换；Linux 保持 `xtyf-manager` 包名。Android 保留原服务器地址设置，新的原生会话首次需要登录。

服务器地址支持 HTTP/HTTPS 根地址及自定义端口，按输入的协议连接，HTTPS 正常校验证书。客户端不再把默认域名的 HTTP 地址自动改为 HTTPS。升级保留已保存的服务器地址；若旧版已保存 HTTPS，可在服务器设置中改为上述 HTTP 地址。切换服务器会取消当前请求、清空页面状态，各服务器会话分别保存。

## 服务器兼容性

客户端需要协议版本 **1**：

- `GET /api/client/bootstrap`：协议版本、登录状态、公开用户信息、权限、主题和业务常量。
- `POST /api/client/login`、`POST /api/client/logout`：JSON 认证接口，复用原有账号、Cookie、CSRF、限流和审计。
- 其余功能继续调用既有业务 API。

旧服务器缺少上述接口时，客户端显示本地“服务器需要更新”提示，保留重试和地址设置，不回退为远程网页外壳。现有浏览器网站继续使用原入口。

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

### 安装包交付

生成安装包前按 [项目交付约定](../AGENTS.md) 询问是否生成及目标版本号。先完成代码修改与必要验证，在项目根目录预览发布入口预计生成的版本：

```powershell
python clients/release.py --dry-run
```

获得用户对生成安装包和版本号的明确确认，且预计版本与确认一致后，再执行：

```powershell
python clients/release.py
```

当前脚本默认递增一次补丁版本和 Android versionCode，尚无指定版本号参数。若用户选择其他版本，先解决发布入口的版本支持，使最终产物与确认一致，再正式构建。先检查构建环境，再构建一次共享前端，四个平台复用同一份资源清单。四个安装包全部构建并检查通过后才替换报告、发布新版和清理旧包；失败恢复版本及原报告，保留旧安装包。

版本来源是 `clients/config.json`，发布入口同步 Tauri 配置、Cargo 和 Android Manifest。不要手工修改版本进行正式交付。

交付记录：

- `dist/clients/build-report.json`：版本、架构、包检查、构建环境及实际运行验证范围。
- `dist/clients/SHA256SUMS.txt`：四个安装包的 SHA-256。
- `clients/frontend/dist/asset-manifest.json`：前端协议、页面和资源哈希；同一次发布的四包使用同一清单。

只读检查、纯文档整理或用户明确要求不打包时无需发布。不推送 GitHub、不部署服务器、不设置后台监听或定时打包。

## 本地构建环境

Python 3.9+（前端构建需要 Jinja2）、Node.js/npm、Visual Studio C++ Build Tools、Rust 1.99.1，以及既有 JDK 17+ / Android platform 36 / build-tools 36。Python 业务回归使用项目 `.venv`。

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

# 准备本地前端（供应商资源已固定版本并记录校验值）
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

## 验证边界

- 业务会话由原生网络层管理，保留 CSRF、服务端权限及认证；站外视图没有业务会话或原生接口权限。
- 安装包检查包含本地资源清单、版本、真实架构、Linux 动态依赖和桌面入口；Ubuntu ARM64 的 GLIBC 上限为 2.35。
- Windows、Android、Ubuntu ARM64、Fedora 的实际启动、导航、上传下载和业务操作分别记录。包检查、APK 签名与对齐通过不能替代目标系统运行验证。
- 交付记录中的运行验证仅在确实执行后标记通过。模拟器测试与真机测试分别说明；未部署服务器不等于线上业务已经验收。

参考：[Tauri 分发](https://v2.tauri.app/distribute/)、[Tauri Linux ARM64 构建](https://v2.tauri.app/distribute/debian/)、[Android 本地内容](https://developer.android.com/develop/ui/views/layout/webapps/load-local-content)。

## 应用图标源文件

客户端图标使用 `static/brand/xtyf-red-blue.jpg` 中的原始红蓝队徽。在安装好固定版本的 Tauri CLI 后，运行 `python clients/build_icons.py` 可按原比例放入白底方形画布，生成桌面的 ICO、128/256 px PNG 和 Android 的 512 px PNG。Android 保留 `@drawable/ic_launcher` 入口，通过 `drawable-nodpi` 中的高清图缩放显示；图标留有边距，不拉伸队徽。此命令仅更新图标资源，不修改版本、不生成安装包；已安装客户端要在下次打包安装后才会使用新图标。
