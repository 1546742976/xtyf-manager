# 项目交付约定

- 生成客户端安装包前（含统一发布及单平台调试重建），由执行任务的代理主动阅读 [客户端发布说明](clients/README.md)，先完成代码修改与必要验证，再询问用户是否生成安装包以及目标版本号。可以运行 `python clients/release.py --dry-run` 展示预计版本和 Android `versionCode`；获得这两项明确确认后才开始打包。用户已在本次交付请求中明确确认生成及版本号时，沿用该确认。
- 用户确认正式发布后，在项目根目录运行 `python clients/release.py`。以 Windows x64 EXE、Android APK、Ubuntu 22.04 ARM64 DEB、Fedora 44/45 共用 x86_64 RPM 四个产物全部构建成功为发布完成条件，并根据 `dist/clients/build-report.json` 和 `dist/clients/SHA256SUMS.txt` 报告版本、产物及实际验收范围。
- 当前发布入口默认递增一次补丁版本和 Android `versionCode`；正式执行前确保预计版本与用户确认一致，若不一致先补齐发布入口对确认版本的支持。四个产物全部成功后才清理旧安装包。构建失败时修复并重试，沿用同一次发布确认、保留旧交付物；明确区分包内容检查与目标系统实际运行验证。
- 用户要求只读或不打包时优先遵守；纯文档整理不产生新版本。单平台构建脚本用于调试重建，不承担版本交付。
- 工作保留在本地，不推送 GitHub。客户端发布不部署服务器；服务器 `HEAD` 源码包仍由 `deploy/package_release.py` 单独负责。完成代码修改或保存文件本身不触发打包，也不设置后台监听或定时任务。
