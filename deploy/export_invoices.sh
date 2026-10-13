#!/usr/bin/env bash
set -euo pipefail
# ============================================================
# 发票整包导出脚本（命令行用；网页端的"导出压缩包"是同一逻辑）
# 用法：/opt/rm-progress/deploy/export_invoices.sh [输出路径]
# 默认输出 /tmp/invoices_export.zip（排除 .uploads / __pycache__ / _待清理）
# ============================================================

APP_DIR="${APP_DIR:-/opt/rm-progress}"
CODE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${INVOICES_DIR:-$APP_DIR/data/invoices}"
OUT="${1:-/tmp/invoices_export.zip}"

python3 - "$SRC" "$OUT" "$CODE_DIR" <<'EOF'
import os
import sys
import zipfile

src, dst = sys.argv[1], sys.argv[2]
sys.path.insert(0, sys.argv[3])
from modules.invoice_storage import InvoiceFileLock
skipped = {'.uploads', '__pycache__', '_待清理'}

count = 0
with InvoiceFileLock(os.path.join(src, '.invoice.lock')):
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in skipped]
            for name in files:
                path = os.path.join(root, name)
                if name in {'.invoice.lock', '往届发票.zip'} or os.path.realpath(path) == os.path.realpath(dst):
                    continue
                z.write(path, os.path.relpath(path, src))
                count += 1

size_mb = os.path.getsize(dst) / 1024 / 1024
print(f"packed {count} files, {size_mb:.1f} MB -> {dst}")
EOF
