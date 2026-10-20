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

# A missing source database must fail loudly: sqlite3 would otherwise create an
# empty file and the "backup" would look successful while restoring nothing.
# Every produced database is also integrity-checked before it is trusted.
backup_database() {
  local source="$1"
  local target="$2"
  if [ ! -f "$source" ]; then
    echo "Backup source is missing: $source" >&2
    return 1
  fi
  sqlite3 "$source" ".backup '$target'"
  local check
  check="$(sqlite3 "$target" 'PRAGMA integrity_check;')"
  if [ "$check" != "ok" ]; then
    echo "Backup integrity check failed: $target ($check)" >&2
    return 1
  fi
  echo "verified: $target"
}

backup_database "$APP_DIR/data.db" "$BK_DIR/data-$STAMP.db"
find "$BK_DIR" -name 'data-*.db' -mtime +14 -delete

if [ -f "$APP_DIR/data/funds/funds.db" ]; then
  backup_database "$APP_DIR/data/funds/funds.db" "$BK_DIR/funds-$STAMP.db"
  find "$BK_DIR" -name 'funds-*.db' -mtime +14 -delete
fi

if [ "$(date +%u)" = "7" ] && [ -d "$APP_DIR/data/invoices" ]; then
  # Use the same file lock as the application so the archive never captures a
  # half-moved PDF or a metadata file mid-write.
  if command -v flock >/dev/null 2>&1; then
    flock -w 120 "$APP_DIR/data/invoices/.invoice.lock" \
      tar czf "$BK_DIR/invoices-$STAMP.tar.gz" -C "$APP_DIR/data" invoices
  else
    echo "flock is unavailable; archiving invoices without the application lock" >&2
    tar czf "$BK_DIR/invoices-$STAMP.tar.gz" -C "$APP_DIR/data" invoices
  fi
  find "$BK_DIR" -name 'invoices-*.tar.gz' -mtime +60 -delete
fi

echo "partial backup done: $BK_DIR/data-$STAMP.db (NOT a unified recovery point)"
