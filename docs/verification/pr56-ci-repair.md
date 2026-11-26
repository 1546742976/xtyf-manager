# PR #56 CI 环境与编码修复（2026-10-25）

基于 `c67172d`，检查运行 `40122900979` 的全部失败作业。

## 原因与修复

- 10 个浏览器分组均在 `actions/setup-python@v5` 初始化 pip 缓存时退出：
  `Unable to locate executable file: lsb_release`。尚未执行浏览器场景。
  外部 fork PR 使用官方 Playwright 1.57.0 容器，其未提供该命令。
  仅移除这个分支的 pip 缓存配置；保留 Python 3.13、Node 22、固定依赖、
  10 个分组和 23 个场景、超时与全部业务断言。原生 Python 作业仍缓存 pip；
  GHCR 预安装路径不变。代价为 fork 浏览器作业重新下载 Python 依赖。
- Windows collaboration 分组 126 项中两项读取 `rm-mail.service` 失败。
  安装器写出 UTF-8，但测试未声明编码，在 Windows CP1252 下遇到中文注释
  抛出 `UnicodeDecodeError`。该测试文件的文本读写统一指定 UTF-8，保留全部断言；
  不通过全局 UTF-8 环境开关隐藏默认编码问题，不改安装器或业务逻辑。

上游 action 源码依据：固定提交
`a26af69be951a213d495a4c3e4e4022e16d87065` 的
`src/cache-distributions/pip-cache.ts` 调用 `getLinuxInfo()`，后者在
`src/utils.ts` 执行 `lsb_release -i -r -s`。

## 本地验证

- 在隔离临时目录运行原有 `test_mail_installation`，只将未显式指定的
  `Path.open` 文本编码模拟为 CP1252；修复前复现同两项 UnicodeDecodeError，
  修复后 17 项中 15 项通过、2 项符号链接权限跳过。未改变系统区域或编码设置。
- Windows Python 3.11.8：`python tests/ci_shards.py collaboration`，
  126 项中 124 项通过、2 项符号链接权限跳过。
- `python -m ruff check tests/test_mail_installation.py`、`git diff --check` 通过。
- 使用现有 WSL PyYAML 解析工作流，确认 10 个浏览器分组、23 个不重复场景及
  对应脚本完整，原生 Python 作业缓存仍启用。

本机默认 Python 无 Flask，测试改用已有项目虚拟环境；Windows 虚拟环境无 PyYAML，
解析改用已有 WSL 工具。首次沙箱测试被文件原子替换权限阻断，使用同一隔离测试
在获准环境复核。Docker daemon 未运行，未宣称本地复现完整 GitHub 容器启动。
本地 Python 3.11 结果不等同于 CI Python 3.13；推送后以新 head 的 GitHub 结果验收。

本次仅 CI 配置及测试修复，不重跑与其无关的业务全量测试来替代真实 CI；
未改版本、未修改业务数据、未部署或本地生成安装包。

## 回滚

`git revert <本修复提交>`。仅恢复工作流缓存配置与测试读写方式，无数据库迁移；
回滚会重新暴露 fork 容器初始化及 Windows 默认编码失败。

## 进入业务测试后的第二轮修复

`6d17f1e` 的远端运行 `40124013314` 已通过 Windows collaboration 和浏览器
初始化，10 个浏览器分组中 7 个通过，3 个资产分组暴露后续错误：

- `assets-serialized` 与 `assets-intake` 的标签打印等待超时。下载 CI 截图确认
  实际错误为无法导入 `/vendor/pdfjs/pdf.mjs`。每个分组都是全新检出，PDF.js
  为忽略的构建资源，原串行测试偶然依靠前面的客户端构建准备它。新增公共准备
  步骤，使用现有 `ensure_vendor_assets()`，适用于 fork 与预构建容器两条路径。
  保留固定版本、锁定 tarball 完整性和逐文件哈希校验，不改 PDF 渲染或下载断言。
- `assets-ledger` 的 WebKit 报告离页请求 access control checks。Windows 本地
  也复现同类错误；除资产 `catalog -> items` 外，还观察到权限保存后的
  `permissions -> audit` 读取链。页面离开后迟到响应仍能发起下一次读取。
  资产页在 pagehide 取消 GET、使旧加载失效；缓存恢复 pageshow 时重新加载。
  共享 security 请求边界拒绝离页后的同源 GET/HEAD。POST、CSRF、待确认写请求
  保留原逻辑，不通过忽略 pageerror 或延长超时将测试标绿。
- 复核客户端页面切换又触发 `Cannot read properties of undefined (reading 'cancel')`：
  相机 pagehide 清理依赖正在卸载窗口的 `AssetScan` 全局。改为保留本页扫描控制器引用，
  确保取消查询后仍释放相机轨道。单测先移除窗口全局复现相同异常，再验证轨道释放。

### 反例与针对性验证

- 新增 Chromium/WebKit 生命周期反例：延迟目录响应，在 pagehide 后释放。
  修复前仍发起 1 次物品查询且信号未取消；修复后要求 0 次查询、信号已取消，
  并验证 pageshow 后恢复正常。事件由测试显式派发，不冒充真实设备 BFCache 验收。
- 共享请求边界单测修复前报 `Missing expected rejection (AbortError)`；
  修复后 Node 22.16.0 全量 143 项通过，覆盖恢复读取、原有 signal、写入 CSRF 和相机释放。
- Windows Playwright 1.57.0：`assets_serialized.cjs`、`assets_catalog_paging.cjs`
  均通过 Chromium/WebKit，包括实际 PDF、25 标签跨页打印、下载字节与认证通道。
- 空临时目录执行资源准备两次，205 个文件全部通过锁定 SHA256，第二次可重复执行。
- `test_asset_pdf_resources.py` 通过，继续拒绝非白名单文件及越界路径。
- `assets.cjs` 最终原始脚本通过 Chromium/WebKit 网站与客户端桥接模拟，包含权限页、
  库存借还、丢失响应恢复、离页/恢复、移动布局与旧服务器能力限制。保留零页面异常断言，
  为后续 CI 失败增加原始 pageerror 栈输出。
- 工作流 YAML 解析、JS 语法、`git diff --check` 通过；保留 10 分组/23 场景。

保留一次本地 WebKit 类别编辑后新增按钮不可见的失败记录；附加状态追踪时未复现，
未据此声称该偶发情况已单独修复。诊断副本、截图及临时资源均不进入提交。
最终远端结果以本轮提交对应的实际 CI 为准。

第二轮回滚：`git revert <第二轮修复提交>`。不涉及数据库；回滚将移除公共资源准备
与页面离开读取保护。第一轮可单独用 `git revert 6d17f1e` 回滚。
