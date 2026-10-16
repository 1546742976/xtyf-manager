# Windows 与 Android 客户端

默认服务器为 **http://manager.skywalker-rm.online**。这两个安装包是已有 Flask 网站的客户端，需要网络和可用的服务器。业务功能与数据取决于服务器当前部署的版本，本地 Python 源码修改不会随客户端自动部署到服务器。

## 安装和使用

- Windows 10/11 x64：运行 `dist/clients/windows/XTYF-Manager-Setup-1.0.1-x64.exe`，选择安装目录。安装器提供开始菜单、桌面入口和卸载程序。“导航、显示、编辑”菜单已删除，按 Alt 也不会显示。连接失败弹窗仍提供服务器地址设置入口。
- Android 8.0 及以上：把 `dist/clients/android/XTYF-Manager-1.0.1.apk` 发送到手机，允许对应文件管理器安装应用后安装。顶部“返回、首页、刷新、设置”按钮栏已移除，返回使用系统返回键或手势；连接失败页面仍可修改服务器地址。APK 不含架构专用的原生库。本次更新的 `versionCode` 为 3，沿用原签名，可覆盖安装。
- 本次交付文件名明确标注 `1.0.1`。更新时安装此版本，安装完成后重新打开应用，避免误用先前下载的 `1.0.0` 同名文件。
- 使用服务器上原有的账号登录。密码和业务权限仍由服务器校验。
- 上传通过系统文件选择器；下载通过文件保存窗口。安卓资金 CSV、发票 CSV/ZIP 与附件下载已增加原始下载端点适配，避免系统 WebView 无法直接保存 blob URL。
- EXE 没有 Windows 发布者证书签名；APK 使用本项目生成的持久发布密钥签名，非 Android 默认 debug 密钥。

当前使用用户指定的 HTTP 地址。客户端允许访问此地址；服务器地址可在连接失败时提供的设置入口中修改，不绕过 HTTPS 证书校验。

## 重新构建

公共配置：`clients/config.json`。修改版本时，同时递增 Android `versionCode`，保留包名和签名密钥，才能覆盖升级。

Windows（Node.js 和 npm）：

```powershell
# 首次或依赖有变化时安装锁定依赖
.\clients\build_windows.ps1 -InstallDependencies
# 此后重建
.\clients\build_windows.ps1
```

脚本使用 `--publish never`，不会上传或推送 GitHub。依赖由 `package-lock.json` 锁定。如果 GitHub 下载慢，可在当前 PowerShell 设置 `ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/` 后构建；构建器仍校验工具包哈希。本次 Electron 镜像文件已与官方 npm 包内的 SHA-256 对照验证。

Android（Python 3.9+、JDK 17+、Android platform 36 / build-tools 36）：

```powershell
python clients/build_android.py
# 也可以指定其他机器上的工具位置
python clients/build_android.py --jdk C:\Tools\jdk-21 --sdk C:\Tools\android-sdk
```

本次下载的构建工具保存在 `.build-tools/`，不需要安装 Android Studio。构建脚本直接调用官方 `aapt2`、`javac`、`D8`、`zipalign` 和 `apksigner`；每次编译前清理仅限 `clients/android/build/` 的临时产物，避免移除的类残留。最终 APK 在 `dist/clients/android/`。

**请单独备份整个 `clients/signing/` 目录**：里面有 Android 签名密钥及其随机密码文件，后续覆盖安装须使用同一密钥。不要把该目录分发给安装包使用者。该目录以及构建工具、依赖、产物均被 `.gitignore` 排除。已有密钥不会在重新构建时被替换。

## 实现范围与验收

- Windows：Electron，远程页面关闭 Node.js，启用沙箱与上下文隔离；只有独立本地设置窗口可以调用设置 IPC。
- Android：系统 WebView，保留服务器 Cookie 和本地存储，不向网页暴露 JavaScript 原生桥；只申请网络权限，使用系统文件选择/保存窗口。
- 安装包仅含客户端源码和运行资源，不含 `data.db`、发票、邮件环境文件、成员数据或 Android 签名私钥。
- 已完成 Windows 配置输入测试；实际启动打包后的 Windows 1.0.1 程序并打开线上登录页，保存截图，确认 Node.js 未暴露、应用菜单为 null、菜单栏不可见且 Alt 后仍不可见。菜单检查在修改前退出码为 4，修改后为 0。
- APK 已通过签名和 zip 对齐验证，已核对包名、版本、启动 Activity、最低 API 和权限。对最终 APK 的 DEX 检查确认：启动布局仅创建竖向根布局、进度条、默认隐藏的连接错误面板和 WebView，没有创建四按钮原生导航栏；新旧 APK 签名证书相同。
- 旧版 versionCode 2 APK 的 DEX 同样未创建四按钮导航栏，尚未复现用户设备上显示旧栏的原因；本次用独立的 1.0.1 文件名和 versionCode 3 消除安装包版本混淆，并进行干净构建。
- 本机未连接 Android 设备，未完成安卓真机启动与上传下载验收；没有使用真实账号验证两端全部业务流程。

构建相关资料：[Electron 安全配置](https://www.electronjs.org/docs/latest/tutorial/security)、[Android 命令行工具](https://developer.android.com/tools)。
