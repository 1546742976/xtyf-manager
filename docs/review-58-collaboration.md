# PR #58 评论处理

- 日常图片选取后在本地解码、最长边缩至 2048、重新编码；通过 Blob / FormData 上传，透明通道保留。浏览器失败保留选图及文字，重试重新处理。
- 只读 preflight 核对账号、执行权限、项目状态及双版本；服务端实际上传在解码前核对一次，在写事务内再次核对，防止预检后权限变化。
- 原生旧客户端仅提供文件句柄，无法提交任意 Blob，明确提示原图安全上传。原生图片预处理不冒充已实现；仍执行相同预检及服务端尺寸、权限、版本检查。
- 指派邮件按收件人及邮箱代次聚合，首条事件后 600 秒到期，不因新事件延长。同一任务更新合并，发送前逐任务复核；已发送、发送中、重试及结果未知的任务不再追加。旧单事件载荷兼容。原有任务权限和任一日常组负责人取消规则保留。

验证（隔离数据库、模拟邮件、合成 PNG）：

- `PYTHONPATH=tests python -m unittest test_assignment_mail test_daily_projects test_task_creator_workflow -v`：最终 38 项；核心两模块 35 项通过，任务创建流程另 3 项通过。
- `node --test tests/js/daily_images.test.cjs`：4 项通过。
- `node tests/browser/daily_mail.cjs`：Chromium 与 Playwright 1.57.0 WebKit 手机模拟通过，覆盖缩放、透明度、预检阻止图片请求、草稿和重试。
- `python clients/build_frontend.py --output-dir .build-tools/review58-client`：27 模板、288 资源构建成功；未生成安装包、未使用原生真机验收。
- Ruff 针对改动 Python 文件通过。真实相机和原生客户端运行仍独立验收。
