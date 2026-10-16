# Windows、Android 与 Linux 客户端

默认服务器为 **http://manager.skywalker-rm.online**。各平台安装包都是已有 Flask 网站的客户端，需要网络和可用的服务器。业务功能与数据取决于服务器当前部署版本；生成客户端安装包不会把本地 Python 源码部署到服务器。

## 安装和使用

- Windows 10/11 x64：运行 `dist/clients/windows/` 下最新的 `XTYF-Manager-Setup-<版本>-x64.exe`。安装器提供开始菜单、桌面入口和卸载程序。应用没有“导航、显示、编辑”菜单，按 Alt 也不会显示；连接失败弹窗提供服务器地址设置入口。
- Android 8.0 及以上：安装 `dist/clients/android/` 下最新的 `XTYF-Manager-<版本>.apk`。应用没有顶部“返回、首页、刷新、设置”按钮栏，返回使用系统返回键或手势；连接失败页面可修改服务器地址。APK 没有架构专用的原生库。
- Ubuntu 22.04 ARM64：使用 `dist/clients/linux/XTYF-Manager-<版本>-arm64.deb`，在包所在目录运行 `sudo apt install ./XTYF-Manager-*-arm64.deb`。
- Fedora 44/45 x86_64：两个版本共用 `dist/clients/linux/XTYF-Manager-<版本>-x86_64.rpm`，在包所在目录运行 `sudo dnf install ./XTYF-Manager-*-x86_64.rpm`。Linux 包提供应用菜单入口，客户端与 Windows 共用 Electron 界面，均没有“导航、显示、编辑”菜单。
- 使用服务器上原有账号登录。密码和业务权限由服务器校验。
- 上传通过系统文件选择器，下载通过文件保存窗口。安卓资金 CSV、发票 CSV/ZIP 与附件下载使用原始下载端点适配。
- EXE 没有 Windows 发布者证书签名。APK 使用本项目持久发布密钥签名；保留包名和同一密钥，配合递增的 `versionCode`，即可覆盖安装。

客户端允许访问用户指定的 HTTP 地址；修改为 HTTPS 地址时仍会校验证书。安装更新后重新打开应用。

## 每次版本交付

从项目根目录运行统一发布入口：

```powershell
# 只读预览下一版本与配置
python clients/release.py --dry-run

# 自动递增版本并构建、交付 EXE、APK、DEB、RPM
python clients/release.py
```

每次成功交付按 `1.0.1 → 1.0.2 → 1.0.3` 的规则递增补丁号，同时将 Android `versionCode` 加 1，无需手改版本。当前版本以 `clients/config.json` 为准。

发布流程先在临时目录构建四个安装包。同一次发布共用一个版本号，只有四个产物都成功，才交付新版并清理旧安装包；任一平台构建失败时恢复原版本配置并保留旧安装包，修复问题后重试。单次成功运行就是一次新版本交付。

交付结果查看：

- `dist/clients/windows/`：Windows 安装包。
- `dist/clients/android/`：Android 安装包。
- `dist/clients/linux/`：Ubuntu ARM64 DEB 和 Fedora x86_64 RPM。
- `dist/clients/build-report.json`：本次成功构建的版本和产物信息。
- `dist/clients/SHA256SUMS.txt`：交付文件校验值。

后续代码或客户端版本交付时，由执行任务的代理主动运行此入口。只读检查、文档整理或用户明确要求不打包时，无需生成新版本。这里的自动化由交付时运行发布命令触发，不会在每次保存文件时打包，也没有后台定时监听；脚本不会推送 GitHub，也不会发布服务器。

服务器源码包仍由独立的 `python deploy/package_release.py` 生成，内容来自当前 Git `HEAD`；上传与服务器升级沿用[服务器更新流程](../docs/technical/打包与服务器更新.md)。

## 构建环境与单平台调试

需要 Node.js/npm、Python 3.9+、JDK 17+，以及 Android platform 36 / build-tools 36。本机工具目录为 `.build-tools/`，不需要安装 Android Studio。首次构建或 Windows 依赖变化后，在 `clients/windows/` 运行 `npm.cmd ci --no-audit --no-fund` 安装锁定依赖，再回到项目根目录运行统一发布命令。

Linux 构建默认使用本机 WSL 的 `Ubuntu-22.04` 发行版。首次设置时，在项目根目录的 Windows PowerShell 中运行：

```powershell
# 在 WSL 内安装打包和校验工具
wsl.exe -d Ubuntu-22.04 -u root --exec apt-get update
wsl.exe -d Ubuntu-22.04 -u root --exec apt-get install -y --no-install-recommends rpm binutils cpio xz-utils curl ca-certificates

# 下载并按官方 SHA-256 校验 Node.js 24.22.0，安装至 WSL 用户缓存
python clients/build_linux.py --setup

# 只读检查构建环境
python clients/build_linux.py --check
```

Linux 工具和构建缓存位于 WSL 用户的 `~/.cache/xtyf-manager-build/`。Linux 构建目录及依赖与 Windows 分开，使用锁文件执行 `npm ci`，不会复用 Windows 的 `node_modules` 或 Electron 可执行文件。先解决 `--check` 报告中的缺失工具再发布；已有环境无需每次重复设置。

以下命令仅用于调试或按当前配置重建单个平台，**不会递增版本，也不代替四个产物的版本交付**：

```powershell
# Windows
.\clients\build_windows.ps1

# Android
python clients/build_android.py
# 指定其他机器上的 JDK / SDK
python clients/build_android.py --jdk C:\Tools\jdk-21 --sdk C:\Tools\android-sdk

# Linux：同时构建 ARM64 DEB 和 x86_64 RPM
python clients/build_linux.py
# 输出到指定临时目录
python clients/build_linux.py --output-dir .build-tools/linux-debug
```

Windows 构建使用锁定依赖和 `--publish never`。Android 构建直接调用官方 `aapt2`、`javac`、`D8`、`zipalign` 和 `apksigner`，每次编译前清理 `clients/android/build/` 的临时产物。

Linux 使用相同锁定版本的 Electron 和 electron-builder，分别获取 ARM64、x64 的 Electron 运行资源并生成对应架构安装包。维护者为 `巡天御风 <1546742976@qq.com>`，项目主页为默认服务器网址。Linux 安装包由本地发布流程生成，不上传软件源。

**请单独备份整个 `clients/signing/` 目录**：其中的 Android 签名密钥及随机密码文件是后续覆盖安装所需凭据。已有密钥不会在重新构建时被替换。签名目录仅供维护者使用；它与构建工具、依赖和产物均被 `.gitignore` 排除。

## 实现范围与验收

- Windows 和 Linux 使用 Electron，远程页面关闭 Node.js，启用沙箱与上下文隔离；只有独立本地设置窗口可以调用设置 IPC。
- Android 使用系统 WebView，保留服务器 Cookie 和本地存储，不向网页暴露 JavaScript 原生桥；只申请网络权限，使用系统文件选择与保存窗口。
- 安装包只包含客户端源码和运行资源，不包含 `data.db`、发票、邮件环境文件、成员数据或 Android 签名私钥。
- Linux 包检查涵盖版本、真实架构、运行依赖、桌面入口及 ARM64 ELF 的 GLIBC 要求；Ubuntu 22.04 的兼容性上限按 GLIBC 2.35 检查。包管理器查询和 ELF 检查不能代替目标系统实际启动。
- 构建报告和 SHA-256 校验值用于确认本次产物；构建成功、APK 签名与对齐校验不等于真机或全部业务流程验收。交付时分别说明实际完成的 Windows 启动、Android 真机、Ubuntu ARM64、Fedora 桌面及业务检查。未运行的平台明确标记为未验证。
- 本次新增 Linux 包时（2026-10-16），Fedora 45 仍处于发布前阶段；相关检查应注明使用的预发布版本，不能作为正式版桌面运行通过的证明。发布状态以 [Fedora 发布日历](https://calendar.fedoraproject.org/list/Fedora%20release/) 为准。

构建相关资料：[Electron 安全配置](https://www.electronjs.org/docs/latest/tutorial/security)、[Android 命令行工具](https://developer.android.com/tools)、[electron-builder Linux 构建](https://www.electron.build/v26/docs/linux/)。

RPM 依赖名称按 Fedora 官方软件包索引核对，包括 [gtk3](https://packages.fedoraproject.org/pkgs/gtk3/gtk3/)、[mesa-libgbm](https://packages.fedoraproject.org/pkgs/mesa/mesa-libgbm/)、[cups-libs](https://packages.fedoraproject.org/pkgs/cups/cups-libs/)；索引存在不代表已在目标系统完成安装或运行验证。
