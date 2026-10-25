# v1.1.1 七目标客户端交付

版本：v1.1.1；Android versionCode：15。

技术栈保持 Tauri 2 桌面端、Android 系统 WebView。本次仅本地生成安装包，未推送、未部署服务器、未修改业务数据库。旧 v1.1.0 交付物仍保留在主工作区 dist/clients。

## 产物与校验

| 产物 | 字节数 | SHA-256 |
|---|---:|---|
| windows/XTYF-Manager-Setup-1.1.1-x64.exe | 8461557 | `e4b3a6de5deb887fc0cced93d00b7348746e3f346ea4b9b856aaa2917f1646cb` |
| windows/XTYF-Manager-Setup-1.1.1-arm64.exe | 8110511 | `8dfecea93163bdc14cc47335611fb94a1457d8f6b97e84980fd5740d015fecfe` |
| android/XTYF-Manager-1.1.1.apk | 5663705 | `bf3d77519a1468630b368aaf59a259e022e0b84c4ac9d7c7b64116fc2d0032d5` |
| linux/XTYF-Manager-1.1.1-arm64.deb | 10042808 | `b74460264125853a38b7b8c5828f0bb55afbb25bb3f9d8223a433c585b96d70b` |
| linux/XTYF-Manager-1.1.1-x86_64.rpm | 10201834 | `5495142d9bc454fcb2917754b4e5fd0dc0ab8298ad0b2db91f2a21ae1bb28eca` |
| linux/XTYF-Manager-1.1.1-aarch64.rpm | 10043940 | `e2668397483164ccc3a31bc5c3aa13f89d60df42dac60a74af92dd2986652fdc` |
| linux/XTYF-Manager-1.1.1-amd64.deb | 10200648 | `8fabbb553a28c07b9f79253e5e257ae41981b6f0cdbebcab4dde190709c6d7fd` |

完整报告：当前工作区 `dist/clients/build-report.json`；校验和：`dist/clients/SHA256SUMS.txt`。七包生成后再次逐文件校验长度与 SHA-256，全部一致。

七包共用前端清单 SHA-256：`e9779894a73134277ce8a070371e8216df81b02ea7997f332233e38588323d88`。

Android 正式证书 SHA-256：`5d7970f1c903ac58b0da93d6b6c53bdb41558564a331bb91e8928039bdcd872b`，与旧 APK 实际证书一致；签名与对齐均通过，未使用测试签名。Windows EXE 未进行代码签名。

## 实际验证范围

- `python -m unittest clients.test_release -q`：30 项发布回归通过，包含七包齐备、失败回滚和旧包保留。
- `python -m unittest clients.test_build_linux clients.test_build_linux_native -v`：3 项 Linux 构建检查通过。
- `cargo test --locked`：Windows 35 项原生测试通过；两种 EXE 都完成真实 PE 架构与 NSIS 解包对照。Windows ARM64 未在 ARM64 Windows 上运行。
- Android `test_transport.py`：89 个断言通过；`test_ui_updates.py --fixture ...`：88 个断言通过，Activity、传输及更新模块通过 Android SDK 36 编译。
- `clients/desktop/test_camera_runtime.cjs`：最终 v1.1.1 同源码调试程序的 Windows x64 WebView2 模拟视频采集通过；单独无 fake-UI 的新进程确认麦克风返回 NotAllowedError。使用隔离配置和模拟媒体，不访问真实摄像头。
- 四个 Linux 包完成 ELF 架构、依赖、桌面入口、前端清单与包内二进制对照；实际要求 GLIBC 2.34，低于 Ubuntu 22.04 的 2.35 上限。x86_64 包执行了原生 --build-info；ARM64 只进行包与二进制检查。
- Windows ARM64、Android 真机、Ubuntu/Fedora 的图形界面与真实摄像头尚未运行验收。模拟摄像头、编译和静态检查不能替代目标设备授权与驱动验证。

## 摄像头行为

Windows 使用 WebView2 授权；Linux 显式启用 WebKitGTK 媒体采集并显示确认窗口，声明 GStreamer Good 摄像头依赖；Android 保留 CAMERA 运行时授权和页面来源检查。范围为本地页面与配置的 HTTPS 网站，拒绝外链视图和麦克风。HTTP 网站模式继续使用相册或手动输入，不关闭安全上下文校验。用户仍需允许系统权限，系统隐私设置及驱动也会影响取流。

参考：[WebKitGTK 媒体采集设置](https://webkitgtk.org/reference/webkit2gtk/2.42.5/property.Settings.enable-media-stream.html)、[GStreamer Video4Linux 插件](https://gstreamer.freedesktop.org/documentation/video4linux2/index.html)。

## 构建追溯与回滚

构建源提交：`4dca88991a0b90d91b000d5454d54b09dc92e9fc`，构建开始时工作区干净；统一发布随后同步七处版本元数据。

- `b8f2be7`：七目标发布与桌面摄像头授权；回滚：`git revert b8f2be7`。
- `4dca889`：ARM64 Clang 预检查与 WebView2 模拟测试；回滚：`git revert 4dca889`。
- 本文与 v1.1.1 元数据由交付提交保存；需要代码回滚时先撤销交付提交，再按时间倒序撤销上述提交。不要删除业务数据库、附件或头像。

构建环境：Windows x64、MSVC 14.51 ARM64 工具、Clang 22.1.3、Rust 1.99.1、Android SDK/build-tools 36、JDK 21；Linux 构建使用 Ubuntu 22.04 WSL 与 ARM64 sysroot。ARM64 Clang 从微软官方 VSIX 以 HTTPS 按需提取，并校验各 ZIP 条目长度/CRC，未将编译工具或签名私钥提交到 Git。
