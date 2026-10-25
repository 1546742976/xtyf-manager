## 正式发布审核 · Release approval

合并本 PR 后将启动正式版发布。请核对版本和更新内容。

| 项目 | 值 |
|---|---|
| 目标版本 | `{{VERSION}}` |
| 上一正式版 | `{{PREVIOUS}}` |
| Android versionCode | `{{ANDROID_CODE}}` |

### 审核清单
- [ ] 已核对本版功能、修复及兼容性变更
- [ ] Python / JavaScript / 浏览器 CI 检查通过
- [ ] Windows x64/ARM64、Linux x64/ARM64、Android 打包通过
- [ ] Android 使用原正式签名证书，验证覆盖安装
- [ ] 发布说明与测试结果符合预期

### 合并后的操作
工作流固定合并提交为发布源码，运行签名构建、校验七个安装包的 SHA-256，并创建 `v{{VERSION}}` 正式 Release。

PR 环境绝不使用正式签名密钥。