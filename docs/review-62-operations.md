# PR #62 评论处理与隔离运行记录

邮件 timer 的 `OnBootSec`、`OnUnitInactiveSec` 均恢复为 15 秒。没有修改业务邮件时间、FRP、监听端口或生产服务；默认端口继续为 80。

`deploy.sh` 与 `update_release.py` 共用 `deploy/health_url.py`。HTTP/HTTPS URL 必须无凭据、片段、空白且主机和端口有效；更新入口在停止服务前拒绝非法 URL。更新器不跟随 301/302，只有服务 active 且原请求直接返回 200 才通过，超时和失败仍非零退出。

## 回归

`python -m unittest discover -s tests -p test_deploy_health.py -v` 与 `test_mail_installation.py`：Windows Python 3.11 共 25 项，21 通过、4 个 Bash 场景跳过；Ubuntu WSL Python 3.13 同一组 25 项全部通过。覆盖真实本地 HTTP 301/302 跳到 200 仍拒绝、非法地址提前退出、默认 80、连接失败、非 active、晚启动恢复及 timer 安装幂等。

补齐时间实验对审计新规则的适配：预览不删行，显式确认才清理；严格截止边界及 pending 保护保留。四项固定时刻测试通过。原“部分成员导入成功”的接口和浏览器断言已在 #60 改为整批拒绝；它们不能继续作为新行为的验收标准。

## 真实 systemd 实验

显式运行 `tests/systemd_hardening_probe.py`，不会被普通 unittest 自动执行。2026-10-25 在 Ubuntu-22.04 WSL 的真实 systemd 一次性 unit 中使用 `NoNewPrivileges=yes`、`ProtectSystem=strict`、`PrivateTmp=yes`、只读源码路径及受限写目录。增加 `PrivateNetwork=yes` 避免外部服务和生产端口，赋予 `CAP_NET_BIND_SERVICE` 验证默认 80。

实际 Gunicorn（1 worker/8 threads）完成 HTTP 登录页和合成账号登录，随后同一沙箱进程内执行附件、日常项目、发票提交/恢复和模拟指派邮件 61 项回归，全部通过；SQLite `integrity_check` 为 ok。写库、图片和发票为合成夹具，邮件未发送。日志中的故障注入异常为预期断言。

首次将 Gunicorn 心跳临时文件置于 E 盘 WSL 挂载目录时，已 unlink 文件描述符的 `os.utime` 报 ENOENT，启动失败。实验修正为生产通常使用的 Linux `/tmp` 后通过，业务夹具位置仍受控，没有为通过测试放宽 systemd 保护。实验 HOME 也独立，避免 Gunicorn 控制套接字接触现有用户目录。

这验证了真实 systemd 下的上述具体流程，不等同于生产部署、FRP 拓扑、长时间负载或目标 Linux 安装包现场验收。代码回滚保留数据库和业务文件；撤销健康检查修复会恢复重定向误判风险。
