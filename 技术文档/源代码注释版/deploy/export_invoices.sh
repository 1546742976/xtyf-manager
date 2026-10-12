#!/usr/bin/env bash
set -euo pipefail
# ============================================================
# 发票整包导出脚本（命令行用；网页端的"导出压缩包"是同一逻辑）
# 用法：/opt/rm-progress/deploy/export_invoices.sh [输出路径]
# 默认输出 /tmp/invoices_export.zip（排除 .uploads / __pycache__ / _待清理）
# ============================================================

SRC="/opt/rm-progress/data/invoices"
OUT="${1:-/tmp/invoices_export.zip}"

python3 - "$SRC" "$OUT" <<'EOF'
import os
import sys
import zipfile

src, dst = sys.argv[1], sys.argv[2]
skipped = {'.uploads', '__pycache__', '_待清理'}

if os.path.exists(dst):
    os.remove(dst)

count = 0
with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in skipped]
        for name in files:
            path = os.path.join(root, name)
            z.write(path, os.path.relpath(path, src))
            count += 1

size_mb = os.path.getsize(dst) / 1024 / 1024
print(f"packed {count} files, {size_mb:.1f} MB -> {dst}")
EOF
