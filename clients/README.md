# 本地界面客户端：Tauri 桌面端与 Android

Windows、Ubuntu、Fedora 使用 Tauri 2，Android 使用系统 WebView。登录页、全部业务页面、脚本、样式、字体和 PDF 阅读器随安装包内置。客户端只向配置服务器请求数据和文件；主界面不加载服务器 HTML，也不使用在线 CDN。

默认服务器为 **https://manager.skywalker-rm.online**。断网可以打开本地界面和服务器设置，业务操作需要联网。站外 HTTP/HTTPS 链接在 App 内独立浏览视图打开，具有返回、关闭入口，不调用系统浏览器。图片及 PDF 在 App 内预览，其余附件通过系统保存窗口下载。

## 安装和升级

- Windows 10/11 x64：`dist/clients/windows/XTYF-Manager-Setup-<版本>-x64.exe`。NSIS 安装器提供快捷方式和卸载入口；缺少 WebView2 时联网补齐运行时。EXE 未使用发布者证书签名。
- Android 8.0+：`dist/clients/android/XTYF-Manager-<版本>.apk`。保留包名 `online.skywalker.manager` 和既有发布密钥，递增 versionCode 后覆盖安装。返回使用系统返回键或手势。
- Ubuntu 22.04 ARM64：`sudo apt install ./XTYF-Manager-<版本>-arm64.deb`。
- Fedora 44/45 x86_64：`sudo dnf install ./XTYF-Manager-<版本>-x86_64.rpm`。

桌面升级保留服务器地址，首次从 Electron 迁移需要重新登录。Windows 安装器识别旧版 Electron 的卸载记录，保留用户数据后完成替换；Linux 保持 `xtyf-manager` 包名。Android 保留原服务器地址设置，新的原生会话首次需要登录。

服务器地址支持 HTTP/HTTPS 根地址及自定义端口，HTTPS 正常校验证书。旧版保存的默认服务器 HTTP 地址（80 端口）自动迁移为 HTTPS，避免该站点的 HTTPS 跳转被跨来源保护拦截；其他服务器和自定义端口保持原配置。切换服务器会取消当前请求、清空页面状态，各服务器会话分别保存。

## 服务器兼容性

客户端需要协议版本 **1**：

- `GET /api/client/bootstrap`：协议版本、登录状态、公开用户信息、权限、主题和业务常量。
- `POST /api/client/login`、`POST /api/client/logout`：JSON 认证接口，复用原有账号、Cookie、CSRF、限流和审计。
- 其余功能继续调用既有业务 API。

旧服务器缺少上述接口时，客户端显示本地“服务器需要更新”提示，保留重试和地址设置，不回退为远程网页外壳。现有浏览器网站继续使用原入口。

**构建客户端不会部署服务器。** 服务器上线必须包含本次接口源码；服务器源码包仍通过 `python deploy/package_release.py` 从 Git HEAD 独立生成，未提交的代码不会自动进入该源码包。线上兼容性必须在服务器实际更新后验证。

## 统一版本交付

在项目根目录运行：

```powershell
python clients/release.py --dry-run
python clients/release.py
```

每次成功发布只递增一次补丁版本和 Android versionCode。先检查构建环境，再构建一次共享前端，四个平台复用同一份资源清单。四个安装包全部构建并检查通过后才替换报告、发布新版和清理旧包；失败恢复版本及原报告，保留旧安装包。

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

单平台命令只用于按当前版本调试，不代替四包正式发布：

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
