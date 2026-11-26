# Client 发布流程

- PR：\`CI\` 运行后端测试；客户端相关变化由 \`Client validation\` 执行测试和临时签名安装包打包，成果只在 Artifacts。
- 合并普通客户端改动到 main：\`Client release\` 串行分配 \`v1.2.0-beta.N\` 等版本，临时注入一致的版本元数据并打包、验证七个安装包；只有全部成功才发布 Pre-release。
- 准备正式版：Actions → \`Prepare stable release PR\` → Run workflow。工作流生成 \`release/X.Y.Z\` Draft PR、同步版本和 Android code、写入发布意图、自动安排 PR 检查。你审核并手动合并后，\`Client release\` 自动发布稳定版。
- 版本目标来自 \`clients/release-plan.json\`。本轮目标 \`1.2.0\`；准备正式版时默认将后续 Beta 指向下一 patch，可在准备 PR 中改为 minor 目标。
- 版本注入只在 Runner 中发生；不为每个 Beta 写代码版本提交。永久版本文件只在正式版准备 PR 中更新。
- Android Beta 和 stable 使用相同的原正式 JKS 签名，使用递增 versionCode。**旧随机签名 Beta 无法直接覆盖升级**。PR 仍用临时签名，不传递生产 Secret。
- 正式/预发布均保留七个安装包，附带 SHA256SUMS.txt、build-report.json。已发布的非空 Release 不会被覆盖，Release 和 Tag 必须指向同一源提交。
- GitHub 默认 GITHUB_TOKEN 创建 PR 不会自动产生 pull_request 工作流，因此准备工作流显式分派 CI 和 Client validation 的 workflow_dispatch 运行。
- Linux DEB 测试版本号使用 \`1.2.0~beta.1\`；RPM 使用 \`1.2.0-0.beta.1\`，均低于 \`1.2.0\` 正式包。
- 安装包上传 GitHub 不等于独立服务器页面的签名热更新，请单独配置发布源。

## 需要的 Actions Secrets
\`ANDROID_KEYSTORE_B64\`（原 JKS 的单行 base64）与 \`ANDROID_KEYSTORE_PASSWORD\`（原 JKS 密码）。应始终备份原签名密钥；失去原密钥无法覆盖升级以前的正式 APK。GitHub Actions 设置必须允许 \`GITHUB_TOKEN\` 写入内容并创建 PR，具体为仓库 Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests。

正式发布 PR 不读取生产 Secrets。正式签名只在受信任的 main 构建中使用；合并涉及 workflow、构建脚本等文件的 PR 前必须审查。发布失败请查看构建日志，不要移动历史标签或强制覆盖 Release 产物。
