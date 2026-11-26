# PR #57 审阅修正

登录默认仅按账号计数。LOGIN_IP_FAILURE_LIMIT、LOGIN_GLOBAL_FAILURE_LIMIT 默认均为 0；不配置 TRUSTED_PROXY_CIDRS，忽略转发头。生产共享代理无需调整 Nginx/FRP。账号六次失败限制仍有效；只有显式配置才启用共享防护。

审计按稳定 endpoint 分类。目前仅 `member_profiles.account_preferences` 的成功主题/外观修改属于 routine，新请求不记业务审计；其拒绝/CSRF失败仍记 critical。邮件偏好、权限、账号、资产、任务、资金发票、导出、登录及未知 action 均保守归 critical。页面默认只列 critical，可通过 API `level=routine/all` 查询旧普通记录。

升级增量增加 `security_audit.level` 并仅在首次建列时回填，不删除任何记录。关键类默认90天，普通类7天，分别用 AUDIT_RETENTION_DAYS / AUDIT_ROUTINE_RETENTION_DAYS 配置，0关闭对应过期清理。AUDIT_MAX_ROWS 只挤出普通类，不删除未到期关键类；pending 和后台任务引用链保护不变。

自动清理已移除。维护者先停止写入，执行 `python deploy/audit_maintenance.py --database <业务库>` 查看分类和待删除计数（预览事务回滚）。核对后使用 `--apply --backup <全新备份路径>`，先做 SQLite 一致性备份再清理；备份失败不删除。`--reset-classification --apply --backup <新备份>` 可将分类保守恢复为全关键类。代码回滚忽略新列；清理删除不可由代码回滚恢复，恢复备份需另行确认。

成员导入在短写锁内先完整校验所有行、有效组别、已有和批内用户名，再插入。任何错误整批拒绝并报告行号；并发冲突全量回滚。组别须预先存在，不自动创建拼写错误的组别。

所有 PR（含 fork）新增 Docker build/smoke，无 GHCR 写入权限；在实际构建的容器内启动 Chromium 与 WebKit。CI 运行结果单独核对，不能用宿主测试冒充镜像构建成功。
