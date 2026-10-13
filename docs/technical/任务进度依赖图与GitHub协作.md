# 任务进度、依赖图与 GitHub 站内协作

## 任务进度

- 任务卡片恢复百分比进度条，显示最新进度更新的时间、作者、备注和图片。
- 任务弹窗内可以手动调整 0～100% 百分比、备注完成的工作并附加最多五张 PNG/JPEG/WebP 图片（每张最多 10 MiB）。
- 进度更新作为独立时间线保存；每条记录有作者、时间、数值、备注和关联图片。已有截图附件保持兼容。
- 有编辑权限的成员可更新进度；其他成员只读。提交使用任务版本号检测并发冲突。进度与任务状态独立。

API:
- GET /api/task/{id}/progress：获取最近 100 条进度记录。
- POST /api/task/{id}/progress：multipart/form-data。字段 progress（0～100 整数）、version（正整数）、note（最多 2000 字）、files（可选，最多五张）。

迁移会新增 task_progress_updates 表与 task_attachments.progress_update_id 可空列。旧任务进度不清零。

## 任务依赖图

任务中心的“任务树”改为有向连线图。前置任务和历史父任务统一作为上游节点；箭头指向后续任务。保留原有 parent_id 和 predecessor_ids 数据结构，避免迁移丢失已有关系。管理员、经理、任务负责人及参与成员在各自权限内都可编辑主前置（父）任务。跨两种关系进行联合循环检测，支持多前置任务；筛选时自动保留上游任务作为上下文。

## GitHub 协作页

在 /discussions 页面内浏览 Issues、Discussions、详情和回复；拥有服务端授权后还能新建主题、直接回复。GitHub 官方页面只作为可选备用链接。

服务端环境变量：

| 变量 | 用途 |
| --- | --- |
| GITHUB_REPOSITORY | 仓库 owner/repository，默认 SHM-white/xtyf-manager |
| GITHUB_TOKEN | GitHub API 令牌，保存在服务器端。Discussions GraphQL 读取需要令牌 |
| GITHUB_WRITE_ENABLED | 默认为 0（只读）；显式设成 1 才允许站内代发主题和回复 |

fine-grained PAT 建议只授权目标仓库的 Issues (Read and write)、Discussions (Read and write)、Metadata (Read)。纯阅读使用 Read 权限，并保持写入开关关闭。公开 Issues 不配置 Token 也可读；Discussions 需要 Token。切勿将 Token 写进仓库、模板或浏览器端脚本，配置更新后重启服务。

**重要**：写入使用共享 GitHub 服务账号，工作台会在末尾标识发帖成员的姓名和内部 ID。此功能不代表成员个人 GitHub 身份，请仅对可信成员启用。需要按个人 GitHub 权限发布时应扩展成逐用户 OAuth 授权。

所有远程内容在站内以纯文本转义后显示，避免外部内容直接成为 HTML；不自动上传任务或成员资料。GitHub API 出错时在原页面提示错误。

## 验证

CI 会检查 Python 语法、API 测试、JS 语法和单测。新增测试覆盖进度校验/冲突/截图/权限、混合依赖环路、前端图形与转义，以及 GitHub API mock（测试不会真实发帖）。
