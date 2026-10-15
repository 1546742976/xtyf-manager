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

## GitHub 协作页：只读展示板

工作台 `/discussions` 同步浏览仓库 Issues、Discussions 的公开主题、正文预览与可用的回复。**创建、评论、回复始终在 GitHub 官方网站完成**，成员使用自己的 GitHub 账号登录，不在工作台设置共用的代发身份，也没有写入 GitHub 的 API。

数据源与降级：

| 资源 | 无 Token | 可选只读 GITHUB_TOKEN |
| --- | --- | --- |
| Issues | GitHub REST API：列表、正文、评论（公开仓库直接读取） | 同样可读，可能改善 API 限流 |
| Discussions | GitHub 官方 `/discussions.atom`：近期公开主题及摘要，**不包含完整回复** | GitHub GraphQL：讨论正文和近期回复；读取失败时回退 Atom |

该仓库必须为公开仓库，且已启用 Discussions。Atom 只提供近期主题（默认最多 30 条），不是完整历史库，无法依赖它分页、准确获得评论数或同步所有回复。若当前订阅源不包含某个主题，可以点击 GitHub 站外链接阅读。网络不可用或 API 限流时提供可见错误提示，不假装已同步成功。

服务端环境变量：

| 变量 | 用途 |
| --- | --- |
| `GITHUB_REPOSITORY` | 仓库 `owner/repository`，默认 `SHM-white/xtyf-manager` |
| `GITHUB_TOKEN` | **可选**只读 Token，用于获取 Discussions GraphQL 完整内容，保存在服务器端。公开 Issues / Atom 不依赖它 |

无需配置 `GITHUB_WRITE_ENABLED`；`/discussions/api/post` 已移除，**即使部署环境遗留该变量也不会授权写入**。建议 fine-grained PAT 仅给目标仓库 Discussions 的读取权限，切勿将 Token 写进仓库或浏览器端。

所有远程文本都在前端 HTML 转义；Atom HTML 摘要先剥离标签。用于发帖和查看原帖的链接由已验证的 GitHub 仓库地址生成或经过 GitHub 域名检查。工作台不会自动上传任务、成员或内部数据。


## 验证

CI 会检查 Python 语法、API 测试、JS 语法和单测。新增测试覆盖进度校验/冲突/截图/权限、混合依赖环路、前端图形与转义，以及 GitHub API/Atom 解析和只读外链（测试不会真实发帖）。
