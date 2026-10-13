#!/usr/bin/env bash
set -euo pipefail
# ============================================================
# 数据备份脚本（由 cron 每日 03:17 调用，也可手动执行）
#   - 进度库 / 资金库：sqlite .backup（安全热备份），保留 14 天
#   - 发票数据：每周日打包 tar.gz，保留 60 天
# 本脚本只生成部分/分时备份，不含任务截图和会话密钥！
# 生产升级和可靠恢复请在全部写入服务停止后执行：
#   python3 deploy/consistent_snapshot.py create --app-dir "$APP_DIR" \\
#     --output /private/backups/full-YYYYMMDD.zip --offline-confirmed
# ============================================================

APP_DIR="${APP_DIR:-/opt/rm-progress}"
BK_DIR="$APP_DIR/backups"
mkdir -p "$BK_DIR"

STAMP="$(date +%F)"
sqlite3 "$APP_DIR/data.db" ".backup '$BK_DIR/data-$STAMP.db'"
find "$BK_DIR" -name 'data-*.db' -mtime +14 -delete

if [ -f "$APP_DIR/data/funds/funds.db" ]; then
  sqlite3 "$APP_DIR/data/funds/funds.db" ".backup '$BK_DIR/funds-$STAMP.db'"
  find "$BK_DIR" -name 'funds-*.db' -mtime +14 -delete
fi

if [ "$(date +%u)" = "7" ] && [ -d "$APP_DIR/data/invoices" ]; then
  tar czf "$BK_DIR/invoices-$STAMP.tar.gz" -C "$APP_DIR/data" invoices
  find "$BK_DIR" -name 'invoices-*.tar.gz' -mtime +60 -delete
fi

echo "partial backup done: $BK_DIR/data-$STAMP.db (NOT a unified recovery point)"
