# -*- coding: utf-8 -*-
"""
巡天御风管理系统 - 主程序（进度系统 + 应用总入口）

职责：
  1. 创建 Flask 应用，注册子模块蓝图：资金 /funds、发票 /invoices
  2. 进度系统全部功能：登录、看板、任务、成员、密码、导出
  3. 任务"剩余天数"在 /api/data 中按服务器北京时间实时计算（不落库）

运行：
  本地调试 : python app.py        （默认 8520 端口，见文件底部 __main__）
  线上     : gunicorn -w 1 --threads 8 -b 0.0.0.0:80 app:app（由 systemd 管理）

依赖：common.py（登录态/权限/数据库）、modules/funds.py、modules/invoices.py
"""
# ---- 标准库 ----
import csv
import io
import os
import hashlib
import time
import secrets
import sqlite3
from datetime import datetime, timedelta

from flask import (Flask, Response, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

# ---- 本项目的公共模块与子模块（子模块的蓝图注册见下方 app.register_blueprint）----
from common import (BASE_DIR, DB_PATH, current_user, get_db, login_required,
                    manager_required, now_str)
from modules.funds import funds_bp, can_review
from modules.invoices import bp as invoices_bp
from modules.discussions import discussions_bp
from modules.task_api import bp as task_api_bp
from modules.task_attachments import bp as task_attachments_bp
from modules.task_catalogs import bp as task_catalogs_bp
from modules.task_migrations import migrate as migrate_tasks

# ---- 关键路径与端口（secret.key 用于会话签名，首次运行自动生成）----
SECRET_PATH = os.environ.get("PROGRESS_SECRET_PATH", os.path.join(BASE_DIR, "secret.key"))
PORT = int(os.environ.get("PORT", "8520"))

# ---- 全局常量：任务状态枚举（看板/表单/校验共用）----
STATUSES = ["未开始", "进行中", "已完成", "阻塞", "已取消"]
# ---- 任务优先级（看板上以彩色圆点显示）----
PRIORITIES = ["高", "中", "低"]
# ---- 系统角色：admin=管理员 / manager=项管 / member=成员 ----
ROLE_NAMES = {"admin": "管理员", "manager": "项管", "member": "成员"}
# ---- 会话密钥：读取 secret.key，不存在则生成 64 位随机串并持久化 ----
def _load_secret():
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "r", encoding="utf-8") as f:
            value = f.read().strip()
            if value:
                return value
    value = secrets.token_hex(32)
    with open(SECRET_PATH, "w", encoding="utf-8") as f:
        f.write(value)
    return value


# ---- 应用初始化 ----
app = Flask(__name__)
app.secret_key = _load_secret()
app.json.ensure_ascii = False
app.permanent_session_lifetime = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
app.config.setdefault("GITHUB_REPOSITORY", os.environ.get("GITHUB_REPOSITORY", "SHM-white/xtyf-manager"))
app.config.setdefault("TASK_ATTACHMENTS_DIR", os.environ.get("TASK_ATTACHMENTS_DIR", os.path.join(BASE_DIR, "data", "task_attachments")))

# ---- 注册子模块蓝图（三个系统共用同一登录会话与页面风格）----
app.register_blueprint(funds_bp, url_prefix="/funds")
app.register_blueprint(invoices_bp, url_prefix="/invoices")
app.register_blueprint(task_api_bp)
app.register_blueprint(task_catalogs_bp)
app.register_blueprint(task_attachments_bp)
app.register_blueprint(discussions_bp, url_prefix="/discussions")


@app.context_processor
def inject_funds_reviewer():
    """各模板的待审核角标与实际权限一致。"""
    return {"can_review_funds": can_review()}

# ---- 进度库表结构（data.db）：members 成员 / tasks 任务 / logs 更新日志 ----
SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    group_name TEXT DEFAULT '',
    role TEXT NOT NULL DEFAULT 'member',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    session_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS login_attempts (
    attempt_key TEXT PRIMARY KEY,
    failures INTEGER NOT NULL,
    first_failed_at INTEGER NOT NULL,
    locked_until INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT DEFAULT '',
    owner_id INTEGER,
    group_name TEXT DEFAULT '',
    project TEXT DEFAULT '',
    priority TEXT DEFAULT '中',
    status TEXT DEFAULT '未开始',
    progress INTEGER DEFAULT 0,
    start_date TEXT DEFAULT '',
    due_date TEXT DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    member_id INTEGER,
    member_name TEXT DEFAULT '',
    note TEXT DEFAULT '',
    changes TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""


# ---- 请求结束自动关闭数据库连接（配合 common.get_db 的 g 缓存）----
@app.teardown_appcontext
def _close_db(exc):
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()


# ---- 初始化数据库：建表（幂等，每次启动都会执行）+ 首次运行创建 admin 兜底账号 ----
def init_db():
    db = sqlite3.connect(DB_PATH)
    try:
        db.executescript(SCHEMA)
        # 兼容已有 data.db，不丢账号、不清空密码。
        cols = {row[1] for row in db.execute("PRAGMA table_info(members)")}
        if "session_version" not in cols:
            db.execute("ALTER TABLE members ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
        count = db.execute("SELECT COUNT(*) AS c FROM members").fetchone()[0]
        if count == 0:
            initial_password = os.environ.get("PROGRESS_INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
            if not os.environ.get("PROGRESS_INITIAL_ADMIN_PASSWORD"):
                print("[初始化] admin 初始随机密码：" + initial_password, flush=True)
                print("[初始化] 请登录后修改密码并保护服务器日志。", flush=True)
            db.execute(
                "INSERT INTO members (name, username, password_hash, group_name, role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("管理员", "admin", generate_password_hash(initial_password), "", "admin", now_str()),
            )
            db.commit()
        else:
            db.commit()
        migrate_tasks(db)
    finally:
        db.close()


# ---- 响应头：API 与主页禁止缓存，保证看板"5 秒自动刷新"体验 ----
@app.after_request
def add_no_cache(resp):
    if request.path.startswith("/api/") or request.path == "/":
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ==================== 登录 / 登出 ====================
_LOGIN_WINDOW = 15 * 60
_LOGIN_FAILURE_LIMIT = 6


def _login_attempt_key(username):
    """IP 与用户名共同计数，不信任未经验证的代理转发地址头。"""
    raw = (request.remote_addr or '') + '\x00' + username.casefold()
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _failed_login(db, attempt_key, row, now):
    if row and now - row['first_failed_at'] < _LOGIN_WINDOW:
        failures = row['failures'] + 1
        first = row['first_failed_at']
    else:
        failures, first = 1, now
    locked_until = now + _LOGIN_WINDOW if failures >= _LOGIN_FAILURE_LIMIT else 0
    db.execute(
        "INSERT INTO login_attempts (attempt_key, failures, first_failed_at, locked_until) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(attempt_key) DO UPDATE SET "
        "failures=excluded.failures, first_failed_at=excluded.first_failed_at, "
        "locked_until=excluded.locked_until",
        (attempt_key, failures, first, locked_until),
    )
    # 防止有人不停枚举不存在的用户名导致计数表无限增长。
    db.execute("DELETE FROM login_attempts WHERE first_failed_at < ?", (now - 2 * _LOGIN_WINDOW,))
    db.commit()
    return locked_until > now


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if current_user():
            return redirect(url_for("index"))
        return render_template("login.html", me=None, page="login", role_names=ROLE_NAMES)
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    db = get_db()
    attempt_key = _login_attempt_key(username)
    now = int(time.time())
    attempt = db.execute(
        "SELECT failures, first_failed_at, locked_until FROM login_attempts WHERE attempt_key=?",
        (attempt_key,),
    ).fetchone()
    if attempt and attempt["locked_until"] > now:
        return jsonify(ok=False, error="尝试次数过多，请 15 分钟后重试"), 429
    row = db.execute(
        "SELECT * FROM members WHERE username = ? AND is_active = 1", (username,)
    ).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password):
        locked = _failed_login(db, attempt_key, attempt, now)
        if locked:
            return jsonify(ok=False, error="尝试次数过多，请 15 分钟后重试"), 429
        return jsonify(ok=False, error="用户名或密码错误"), 400
    db.execute("DELETE FROM login_attempts WHERE attempt_key=?", (attempt_key,))
    db.commit()
    session.clear()
    session.permanent = True
    session["uid"] = row["id"]
    session["session_version"] = row["session_version"]
    return jsonify(ok=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ==================== 页面：看板（/）与任务管理（/manage）====================
@app.route("/")
@login_required
def index():
    return render_template("dashboard.html", me=current_user(), page="dashboard", role_names=ROLE_NAMES)


@app.route("/tasks")
@login_required
def tasks():
    return render_template("board.html", me=current_user(), page="tasks", role_names=ROLE_NAMES)


@app.route("/account")
@login_required
def account():
    """所有登录角色的账户设置页面，统一各模块的密码修改入口。"""
    return render_template("account.html", me=current_user(),
                           page="account", role_names=ROLE_NAMES)


@app.route("/manage")
@manager_required
def manage():
    return render_template("manage.html", me=current_user(), page="manage", role_names=ROLE_NAMES,
                           statuses=STATUSES, priorities=PRIORITIES)


@app.route("/members")
@manager_required
def members():
    return render_template("members.html", me=current_user(), page="members", role_names=ROLE_NAMES)


# Export only public directory fields, never hashes, tokens, or temporary passwords.
@app.route("/api/members/export.csv")
@manager_required
def export_members_csv():
    rows = get_db().execute(
        "SELECT m.id, m.name, m.username, "
        "COALESCE((SELECT GROUP_CONCAT(name,';') FROM (SELECT g.name FROM member_groups x "
        "JOIN groups g ON g.id=x.group_id WHERE x.member_id=m.id ORDER BY x.group_id)),'') AS group_name, "
        "m.role, m.is_active, "
        "COALESCE((SELECT GROUP_CONCAT(name,';') FROM (SELECT p.name FROM member_projects mp "
        "JOIN projects p ON p.id=mp.project_id WHERE mp.member_id=m.id ORDER BY mp.project_id)),'') AS project_names, "
        "COALESCE((SELECT GROUP_CONCAT(project_id) FROM member_projects WHERE member_id=m.id),'') AS project_ids, "
        "COALESCE(GROUP_CONCAT(mg.group_id), '') AS group_ids "
        "FROM members m LEFT JOIN member_groups mg ON mg.member_id=m.id "
        "GROUP BY m.id ORDER BY m.group_name, m.id"
    ).fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["ID", "姓名", "用户名", "专业组", "造车组别", "角色", "账号状态", "专业组ID", "造车组别ID"])
    def cell(value):
        # Prevent spreadsheet formula injection for untrusted directory strings.
        value = str(value or "")
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
    for row in rows:
        writer.writerow([row["id"], cell(row["name"]), cell(row["username"]), cell(row["group_name"]), cell(row["project_names"]),
                         cell(ROLE_NAMES.get(row["role"], row["role"])),
                         "启用" if row["is_active"] else "停用", cell(row["group_ids"]), cell(row["project_ids"])])
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="members.csv"'})


@app.route("/api/member/<int:member_id>/reset", methods=["POST"])
@manager_required
def api_member_reset(member_id):
    user = current_user()
    db = get_db()
    row = db.execute("SELECT id, role FROM members WHERE id = ?", (member_id,)).fetchone()
    if row is None:
        return jsonify(ok=False, error="成员不存在"), 404
    if user["role"] != "admin" and row["role"] != "member":
        return jsonify(ok=False, error="无权限"), 403
    data = request.get_json(silent=True) or {}
    generated_password = not bool(data.get("password"))
    password = data.get("password") or secrets.token_urlsafe(10)
    if len(password) < 8:
        return jsonify(ok=False, error="密码至少 8 位"), 400
    db.execute("UPDATE members SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
               (generate_password_hash(password), member_id))
    db.commit()
    return jsonify(ok=True, initial_password=password if generated_password else None)


@app.route("/api/member/<int:member_id>/role", methods=["POST"])
@manager_required
def api_member_role(member_id):
    user = current_user()
    if user["role"] != "admin":
        return jsonify(ok=False, error="仅管理员可调整角色"), 403
    if member_id == user["id"]:
        return jsonify(ok=False, error="不能修改自己的角色"), 400
    data = request.get_json(silent=True) or {}
    role = data.get("role")
    if role not in ROLE_NAMES:
        return jsonify(ok=False, error="角色不合法"), 400
    db = get_db()
    row = db.execute("SELECT id FROM members WHERE id = ?", (member_id,)).fetchone()
    if row is None:
        return jsonify(ok=False, error="成员不存在"), 404
    db.execute("UPDATE members SET role = ?, session_version = session_version + 1 WHERE id = ?", (role, member_id))
    db.commit()
    return jsonify(ok=True)


@app.route("/api/member/<int:member_id>/toggle", methods=["POST"])
@manager_required
def api_member_toggle(member_id):
    user = current_user()
    db = get_db()
    if member_id == user["id"]:
        return jsonify(ok=False, error="不能停用自己"), 400
    row = db.execute("SELECT id, role, is_active FROM members WHERE id = ?", (member_id,)).fetchone()
    if row is None:
        return jsonify(ok=False, error="成员不存在"), 404
    if user["role"] != "admin" and row["role"] != "member":
        return jsonify(ok=False, error="无权限"), 403
    db.execute("UPDATE members SET is_active = ?, session_version = session_version + 1 WHERE id = ?",
               (0 if row["is_active"] else 1, member_id))
    db.commit()
    return jsonify(ok=True)


# ==================== 修改本人密码 ====================
@app.route("/api/password", methods=["POST"])
@login_required
def api_password():
    user = current_user()
    data = request.get_json(silent=True) or {}
    old_password = data.get("old") or ""
    new_password = data.get("new") or ""
    if len(new_password) < 8:
        return jsonify(ok=False, error="新密码至少 8 位"), 400
    db = get_db()
    row = db.execute("SELECT password_hash FROM members WHERE id = ?", (user["id"],)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], old_password):
        return jsonify(ok=False, error="原密码错误"), 400
    db.execute("UPDATE members SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
               (generate_password_hash(new_password), user["id"]))
    version = db.execute("SELECT session_version FROM members WHERE id=?", (user["id"],)).fetchone()[0]
    db.commit()
    session["session_version"] = version
    return jsonify(ok=True)


# ==================== 导出任务 CSV（剩余天数为中文描述：剩余N天/已逾期N天/今天截止）====================
@app.route("/export.csv")
@login_required
def export_csv():
    rows = get_db().execute(
        "SELECT t.id,t.title,COALESCE(m.name,'未分配') AS owner,"
        "COALESCE(p.name,'') AS group_name,"
        "COALESCE(p.name,'') AS project,t.priority,"
        "t.status,t.progress,t.start_date,t.due_date,t.detail,t.updated_at,t.owner_id,t.project_id,"
        "t.parent_id,t.version,COALESCE(t.project_id,'') AS group_ids,"
        "COALESCE(GROUP_CONCAT(DISTINCT tp.member_id),'') AS participant_ids,"
        "COALESCE(GROUP_CONCAT(DISTINCT td.predecessor_id),'') AS predecessor_ids "
        "FROM tasks t LEFT JOIN members m ON m.id=t.owner_id LEFT JOIN projects p ON p.id=t.project_id "
        "LEFT JOIN task_groups tg ON tg.task_id=t.id LEFT JOIN task_participants tp ON tp.task_id=t.id "
        "LEFT JOIN task_dependencies td ON td.task_id=t.id WHERE t.deleted_at IS NULL "
        "GROUP BY t.id ORDER BY t.id"
    ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "任务", "负责人", "组别", "项目", "优先级", "状态", "剩余天数",
                     "开始日期", "截止日期", "说明", "更新时间", "负责人ID", "组别ID",
                     "项目ID", "父任务ID", "参与者ID", "前置任务ID", "版本"])
    def safe_cell(value):
        value = str(value or "")
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
    today = datetime.now().date()
    for row in rows:
        due = (row["due_date"] or "").strip()
        days_text = ""
        if due:
            try:
                diff = (datetime.strptime(due, "%Y-%m-%d").date() - today).days
                if diff < 0:
                    days_text = f"已逾期 {-diff} 天"
                elif diff == 0:
                    days_text = "今天截止"
                else:
                    days_text = f"剩余 {diff} 天"
            except ValueError:
                days_text = due
        writer.writerow([safe_cell(value) for value in (
            row["id"], row["title"], row["owner"], row["group_name"], row["project"],
            row["priority"], row["status"], days_text, row["start_date"], row["due_date"],
            row["detail"], row["updated_at"], row["owner_id"], row["group_ids"], row["project_id"],
            row["parent_id"], row["participant_ids"], row["predecessor_ids"], row["version"],
        )])
    data = "\ufeff" + output.getvalue()
    filename = f"tasks-{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


# ---- 模块导入即初始化（幂等）：gunicorn 启动时自动完成建表与兜底账号 ----
init_db()

# ---- 本地调试入口（线上由 systemd 调用 gunicorn，不会走到这里）----
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
