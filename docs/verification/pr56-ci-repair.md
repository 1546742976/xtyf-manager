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
