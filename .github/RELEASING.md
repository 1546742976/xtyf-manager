# GitHub Actions 安装包与 Release

## 发布方式

- 指向 `main` 的 PR 会执行 `Client validation`。同仓库的 PR 若所有测试和安装包构建成功，自动创建一个 **Pre-release**，名称与标签为 `YYYYMMDD-betaN`（UTC 日期 + 当前工作流的 `run_number`）。每次 push 更新 PR 会创建新的快照，重新运行同一个工作流则使用相同标签。Fork PR 只执行测试，不发布。
- 这些测试包包含 Windows x64/ARM64、Android 通用 APK、Linux x64/ARM64 的 DEB/RPM。**PR 的 APK 使用每次新建的临时签名，不能直接覆盖正式版或其他 CI APK**。请先在独立测试设备使用；这些 Release 不是正式升级渠道。
- 正式发布：先在 `main` 修改 `clients/config.json` 中的版本与 Android `versionCode`，并同步 Tauri/Cargo/Android Manifest 等元数据（参考 `clients/release.py` 的 `sync_versions`）。完成审查后在 `main` 对应提交打 `vX.Y.Z` 标签并推送，触发 `Client release`。也可在 Actions → Client release → Run workflow 输入**已存在**的标签重新构建（但不能覆盖已经存在的 Release）。
- 标签必须与版本文件一致，且正式标签对应提交必须属于 `main` 的历史。正式构建使用固定 Android Keystore。7 个安装包全部成功、校验和一致后才发布稳定 Release；失败时不会开始发布。Release 自动附带 `SHA256SUMS.txt` 和 `build-report.json`。
- 日期 beta 标签只是 **GitHub 测试发布标识**，安装包内部保持 PR 的 `clients/config.json` 版本；不要把不同 beta APK 当成可直接升级的 Android 版本。正式版继续使用 `v1.1.0` 这种三段式语义化版本号。

## Android 固定签名（必须配置）

Android APK **不能完全不签名**；Windows EXE 与 Linux DEB/RPM 不需要购买任何证书。项目正式 APK 使用固定自签名 JKS 即可。**如果已经向成员发过正式 APK，必须找回当时的原始 `xtyf-manager.jks` 和密码；换新密钥会使覆盖安装失败。**

从未对外发布正式 Android APK 时，可在本机安装 JDK 17+，在仓库根目录执行（不要 commit 以下文件）：

```bash
mkdir -p clients/signing
keytool -genkeypair -v -keystore clients/signing/xtyf-manager.jks -storetype JKS -alias xtyf-manager -keyalg RSA -keysize 3072 -validity 10000 -dname "CN=XTYF Manager, OU=Team, O=XTYF, C=CN"
```

按提示输入并记录 Keystore 密码；key password 提示直接回车使用相同密码。将文件安全地离线备份。确认密钥：

```bash
keytool -list -v -keystore clients/signing/xtyf-manager.jks -alias xtyf-manager
```

在 GitHub 仓库 Settings → Secrets and variables → Actions → **New repository secret** 添加：

| Secret | 内容 |
|---|---|
| `ANDROID_KEYSTORE_B64` | JKS 的完整 Base64（单行） |
| `ANDROID_KEYSTORE_PASSWORD` | 上述 Keystore 密码（也用作 key password） |

Linux / WSL 生成 Base64（**不要粘贴到聊天、PR、日志或仓库文件**）：

```bash
base64 -w 0 clients/signing/xtyf-manager.jks
```

Windows PowerShell（将输出复制到 Secrets 表单，而非提交 Git）：

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes((Resolve-Path 'clients/signing/xtyf-manager.jks')))
```

正式版 Actions 在隔离 Runner 上恢复 Keystore，先检查别名与密码，再由原有 `clients/build_android.py` 完成 APK 签名和验证。缺少任一 Secret 将**直接阻止正式 Release**；PR 不读取这两个 Secret。

## 发布命令示例

确保版本元数据已经提交且一致，再运行：

```bash
git switch main
git pull --ff-only
git tag v1.1.0
git push origin v1.1.0
```

仅当当前稳定版本确实是 1.1.0 且尚未创建对应标签时才使用此示例。新版本请改用适当的更高版本与递增的 Android versionCode。

安装包发布**不自动部署服务器**，也不改变独立的、需要 RSA 签名的客户端页面热更新协议。生产下载 URL 如要用于应用内更新，还需额外接入服务器签名更新清单。

## 验收与排错

查看 Actions → Client validation 或 Client release → 运行详情。上传的 Artifacts 在工作流中保留 14 天；Pre-release / Release 附件长期存储于 Releases 页面。PR 的测试包和正式发布包不能混用。跨平台代码编译与包检查通过不等于各真实操作系统上的安装、运行、升级功能已经验收。

如正式 Android 构建在恢复 KeyStore 步骤失败，优先检查仓库 Secrets 是否完整、Base64 是否单行、密钥别名是否为 `xtyf-manager`、原密钥的 key password 是否与 Keystore password 相同。不要通过删掉签名检查或生成新密钥绕过问题。
