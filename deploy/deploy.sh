#!/usr/bin/env bash
set -euo pipefail
# ============================================================
# 一键部署脚本（在树莓派上执行）
#   1) 安装系统依赖（python3-venv/pip/sqlite3/rsync/中文图表字体）
#   2) rsync 源目录(~/progress_system) → 运行目录(/opt/rm-progress)
#      —— 数据(data/)、数据库、secret.key、backups 全部保留，不会被覆盖
#   3) 更新 venv 依赖（清华 pip 镜像）
#   4) 安装网站与邮件 systemd 服务（默认不开邮件）+ 本机自检
# 用法：cd ~/progress_system && bash deploy/deploy.sh
# ============================================================

APP_DIR="${APP_DIR:-/opt/rm-progress}"
APP_USER="${APP_USER:-$(whoami)}"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:80/login}"
STAGE="配置检查"
trap 'echo "部署失败：${STAGE}。服务可能已发生变更，请核对后再继续；未执行自动回滚。" >&2' ERR
python3 - "$HEALTH_URL" <<'PY'
import sys
from urllib.parse import urlsplit
try:
    raw = sys.argv[1]
    url = urlsplit(raw)
    valid = (len(raw) <= 2048 and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in raw)
             and url.scheme in ('http', 'https') and url.hostname
             and url.username is None and url.password is None and not url.fragment)
    url.port
    if not valid:
        raise ValueError()
except ValueError:
    sys.exit('HEALTH_URL 必须是不含账号密码或片段的有效 HTTP/HTTPS 地址')
PY

echo "==> 应用目录: $APP_DIR"
echo "==> 运行用户: $APP_USER"

echo "==> 安装系统依赖"
STAGE="安装系统依赖"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip sqlite3 rsync fonts-noto-cjk

# Legacy direct rsync deployment also embeds Git history when its source is a checkout.
# The signed HEAD release packager creates the same file without touching the checkout.
if command -v git >/dev/null 2>&1 && git -C "$SRC_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "==> 从当前 Git HEAD 生成更新日志快照"
  python3 "$SRC_DIR/deploy/site_updates_snapshot.py" --repo "$SRC_DIR" --output "$SRC_DIR/site-release.json"
fi

echo "==> 同步代码"
STAGE="同步代码"
sudo mkdir -p "$APP_DIR"
# Only synchronize source and explicitly permitted assets; local secrets and build caches stay off the server.
sudo rsync -a --delete \
  --exclude venv --exclude data.db --exclude 'data.db-*' \
  --exclude secret.key --exclude backups --exclude __pycache__ \
  --exclude data --exclude mail.env --exclude index.html \
  --exclude /clients/signing/ --exclude .build-tools --exclude .git \
  --exclude .venv --exclude node_modules --exclude dist \
  --exclude .env --exclude '.env.*' --exclude 'initial-admin-password.txt' --exclude '*.log' --exclude .omo \
  --exclude /clients/desktop/target --exclude /clients/android/build \
  "$SRC_DIR"/ "$APP_DIR"/
sudo chmod +x "$APP_DIR"/deploy/*.sh

echo "==> 创建虚拟环境并安装依赖"
STAGE="安装 Python 依赖"
PIP_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
sudo python3 -m venv "$APP_DIR/venv"
sudo "$APP_DIR/venv/bin/pip" install --upgrade pip -q -i "$PIP_INDEX"
sudo "$APP_DIR/venv/bin/pip" install -q -i "$PIP_INDEX" -r "$APP_DIR/requirements.txt"

sudo chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

echo "==> 安装 systemd 服务"
STAGE="安装与重启服务"
sed "s/{{APP_USER}}/$APP_USER/; s#{{APP_DIR}}#$APP_DIR#g" \
  "$APP_DIR/deploy/rm-progress.service" | sudo tee /etc/systemd/system/rm-progress.service > /dev/null
sudo python3 "$APP_DIR/deploy/install_mail.py" --app-dir "$APP_DIR" --app-user "$APP_USER"
sudo systemctl daemon-reload
# Mail timer is installed but NEVER auto-enabled on first deployment.
sudo systemctl enable rm-progress
sudo systemctl restart rm-progress

echo "==> 放行防火墙端口 80"
sudo ufw allow 80/tcp > /dev/null 2>&1 || true

echo "==> 服务状态"
# Status display is diagnostic; the bounded check below decides success.
sudo systemctl --no-pager status rm-progress || true

echo "==> 本机自检"
STAGE="健康检查"
healthy=0
for attempt in $(seq 1 30); do
  http_code=000
  if code=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
      --connect-timeout 2 --max-time 3 "$HEALTH_URL"); then
    http_code="$code"
  fi
  if sudo systemctl is-active --quiet rm-progress && [[ "$http_code" == 200 ]]; then
    healthy=1
    break
  fi
  echo "健康检查未通过（${attempt}/30，HTTP ${http_code}）" >&2
  if [[ "$attempt" -lt 30 ]]; then sleep 1; fi
done
if [[ "$healthy" != 1 ]]; then
  echo "部署失败：服务可能已更新，但健康检查未通过。请核对服务与日志；未执行自动回滚。" >&2
  exit 1
fi

echo "==> 完成。局域网访问: http://<树莓派IP>"
