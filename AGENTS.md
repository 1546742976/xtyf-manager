# 项目交付约定

- 交付代码或客户端新版本前，由执行任务的代理主动阅读 [客户端发布说明](clients/README.md)，在项目根目录运行 `python clients/release.py`。以 Windows x64 EXE、Android APK、Ubuntu 22.04 ARM64 DEB、Fedora 44/45 共用 x86_64 RPM 四个产物全部构建成功为完成条件，并根据 `dist/clients/build-report.json` 和 `dist/clients/SHA256SUMS.txt` 报告版本、产物及实际验收范围。
- 发布入口每次只递增一次补丁版本和 Android `versionCode`，四个产物全部成功后才清理旧安装包。构建失败时先修复并重试，保留旧交付物；明确区分包内容检查与目标系统实际运行验证。
- 用户要求只读或不打包时优先遵守；纯文档整理不产生新版本。单平台构建脚本用于调试重建，不承担版本交付。
- 工作保留在本地，不推送 GitHub。客户端发布不部署服务器；服务器 `HEAD` 源码包仍由 `deploy/package_release.py` 单独负责。自动发布在版本交付时触发，不在每次保存文件时打包，也不设置后台监听或定时任务。
