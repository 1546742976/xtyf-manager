# PR #61 评论修复验收

## Android PDF 保存

MainActivity 先占用当前保存请求，在既有 worker 中校验并解码 `pdfBody`，成功后才由 UI 线程打开保存位置选择器。PdfSave.Prepared 持有一次已校验结果，选定位置后直接消费字节，不再第二次解码；保留 20 MiB 上限、PDF 头和 Base64 校验。取消、页面/服务器切换释放内容，迟到结果不能重新打开窗口。旧选择器还未返回时禁止打开另一个保存选择器，避免旧 activity result 被新请求接收。写入时分块复核页面和服务器身份。

不含 pdfBody 的原认证下载路径保持；正式固定签名和测试签名机制未修改，不读取、生成或覆盖正式密钥。本次未生成安装包。

## 桌面摄像头边界

现有 Tauri 回调仅有 Webview 和 PermissionKind，camera.rs 检查顶层 document URL，未提供请求 iframe 的 Origin。继续拒绝非 Camera 权限及外域导航，没有将顶层 URL 冒充请求方 Origin。

`node tests/browser/camera_origin_probe.cjs` 使用 Playwright 1.57.0 Chromium 虚拟摄像头和两个不同 localhost origin：无委托 iframe 为 NotAllowedError；显式 allow=camera 的 iframe 获准。这说明页面权限策略是重要边界，当前 native 回调不能额外确认子框架来源。已纳入浏览器 CI。不是 Windows WebView2/Linux WebKitGTK 现场验证，也不能证明所有原生平台拒绝跨域委托；按评论作为非阻塞剩余风险记录。

## 自动化结果（2026-10-25）

- WSL Ubuntu 22.04 / OpenJDK 11：`python3 clients/android/test_transport.py`，98 个断言通过（20 MiB、非法内容、取消、过期页面/服务器、旧下载 HTTP 流程）。
- `python3 clients/android/test_ui_updates.py --jdk /usr/lib/jvm/java-11-openjdk-amd64 --json-jar <测试JAR> --android-jar <SDK36-JAR>`：84 个断言通过，全部 Android Activity/transport/update store 编译通过。仅有现存 deprecated API 编译提示。
- SDK 平台来自 Google 官方 platform-36-ext19_r01.zip，校验其仓库声明 SHA1 后仅解出 android.jar；org.json 使用现有固定 SHA256 的 20240303 测试 JAR。均在忽略目录，不进入提交。
- 无可用 Rust 编译器，本次无 Rust 源码改动；真实 Android UI 卡顿、保存选择器和目标桌面原生摄像头仍需设备验收。

回滚：revert 本提交即可恢复先前实现；不涉及数据库、签名、版本或用户数据。
