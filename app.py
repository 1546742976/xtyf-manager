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
import re
import sqlite3
from datetime import datetime, timedelta

from flask import (Flask, Response, g, jsonify, redirect, render_template,
                   request, session, url_for, abort, send_from_directory)
from werkzeug.security import check_password_hash, generate_password_hash

# ---- 本项目的公共模块与子模块（子模块的蓝图注册见下方 app.register_blueprint）----
from common import (BASE_DIR, DB_PATH, current_user, get_db, login_required,
                    manager_required, error_response, now_str, ROLE_NAMES as COMMON_ROLE_NAMES)
from modules.funds import funds_bp, can_review
from modules.input_validation import valid_password
from modules.client_address import client_address, parse_trusted_proxies
from modules.task_validation import InvalidIdError, parse_id
from modules.invoices import bp as invoices_bp, migrate_invoice_owners, start_invoice_worker
from modules.invoice_jobs import migrate as migrate_invoice_jobs
from modules.discussions import discussions_bp
from modules.collaboration_schema import migrate as migrate_collaboration
from modules.task_api import bp as task_api_bp
from modules.task_mutations import PRIORITIES as TASK_PRIORITIES, STATUSES as TASK_STATUSES
from modules.task_attachments import bp as task_attachments_bp
from modules.task_progress import bp as task_progress_bp
from modules.task_catalogs import bp as task_catalogs_bp
from modules.member_profiles import bp as member_profiles_bp
from modules.member_avatars import bp as member_avatars_bp, migrate as migrate_avatars
from modules.task_migrations import migrate as migrate_tasks
from modules.daily_schema import migrate as migrate_daily
from modules.daily_projects import bp as daily_projects_bp
from modules.task_alerts import bp as task_alerts_bp
from modules.item_topics import bp as item_topics_bp, migrate as migrate_item_topics
from modules.email_api import bp as email_bp
from modules.mail_service import configure as configure_mail
from modules.security import cleanup_audit_rows, init_security, migrate_security, write_initial_credentials
from modules.workspace_settings import bp as workspace_settings_bp, migrate as migrate_workspace, theme_context
from modules.client_api import create_client_blueprint
from modules.client_updates import bp as client_updates_bp
from modules.site_updates import bp as site_updates_bp

# ---- 关键路径与端口（secret.key 用于会话签名，首次运行自动生成）----
SECRET_PATH = os.environ.get("PROGRESS_SECRET_PATH", os.path.join(BASE_DIR, "secret.key"))
PORT = int(os.environ.get("PORT", "8520"))

# ---- 全局常量：任务状态枚举（看板/表单/校验共用）----
STATUSES = list(TASK_STATUSES)
# ---- 任务优先级（看板上以彩色圆点显示）----
PRIORITIES = list(TASK_PRIORITIES)
# ---- 系统角色：admin=管理员 / manager=项管 / member=成员 ----
ROLE_NAMES = COMMON_ROLE_NAMES.copy()
# ---- 会话密钥：读取 secret.key，不存在则生成 64 位随机串并持久化 ----
def _load_secret():
    """Create the session-signing key privately; never overwrite an existing key.

    An empty or symlinked key file is a startup error, not a reason to silently
    change keys and invalidate everybody's sessions. O_EXCL prevents startup
    races between Gunicorn processes from replacing one another's key.
    """
    import time

    def read_existing():
        if os.path.islink(SECRET_PATH):
            raise RuntimeError("会话密钥路径不能是符号链接")
        with open(SECRET_PATH, "r", encoding="utf-8") as stream:
            value = stream.read().strip()
        if value and os.name == "posix":
            os.chmod(SECRET_PATH, 0o600)
        return value

    if os.path.lexists(SECRET_PATH):
        existing = read_existing()
        if not existing:
            raise RuntimeError("会话密钥文件为空，请检查密钥备份后人工处理")
        return existing

    value = secrets.token_hex(32)
    try:
        fd = os.open(SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process is initializing the key. Wait for the complete
        # value rather than generating/replacing the file.
        for _ in range(100):
            try:
                existing = read_existing()
            except FileNotFoundError:
                existing = ""
            if len(existing) == 64:
                return existing
            time.sleep(0.05)
        raise RuntimeError("会话密钥初始化未完成，请检查密钥文件")
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    return value


# ---- 应用初始化 ----
app = Flask(__name__)
app.config['TRUSTED_PROXY_NETWORKS'] = parse_trusted_proxies(os.environ.get('TRUSTED_PROXY_CIDRS', ''))
app.secret_key = _load_secret()
app.json.ensure_ascii = False
app.permanent_session_lifetime = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config.setdefault("TASK_ATTACHMENTS_DIR", os.environ.get("TASK_ATTACHMENTS_DIR", os.path.join(BASE_DIR, "data", "task_attachments")))
configure_mail(app)

@app.before_request
def request_body_limit():
    if 'member_id' in (request.view_args or {}):
        try:
            parse_id(request.view_args['member_id'], '成员 ID', False)
        except InvalidIdError as exc:
            return jsonify(ok=False, error=str(exc)), 400
    # Run before CSRF/audit hooks: both may parse form or JSON bodies.
    if request.method in {'GET', 'HEAD', 'OPTIONS'}:
        return None
    limits = {
        'invoices.api_upload': 200, 'invoices.api_upload_batch': 200,
        'discussions.create_topic': 70, 'discussions.create_message': 70,
        'task_attachments.upload_attachments': 55,
        'task_progress.update_progress': 55, 'daily_projects.progress': 55,
        'member_avatars.account_avatar': 12,
    }
    limit = limits.get(request.endpoint, 16) * 1024 * 1024
    if request.path.startswith('/api/assets/'):
        limit = 16 * 1024
    request.max_content_length = limit
    if request.content_length is not None and request.content_length > limit:
        return error_response(413, '请求内容过大，请减少文件或内容后重试')
    return None


@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(413)
@app.errorhandler(500)
def friendly_error(error):
    messages = {403: '无权限访问此页面', 404: '页面或资源不存在', 405: '不支持此请求方式',
                413: '请求内容过大，请减少文件或内容后重试', 500: '服务暂时不可用，请稍后重试'}
    if error.code == 500:
        app.logger.error('请求处理失败: %s', request.path, exc_info=error.original_exception)
    return error_response(error.code, messages[error.code])


_WEB_VENDOR_FILES = {
    'bootstrap/LICENSE', 'bootstrap/bootstrap.min.css', 'bootstrap/bootstrap.bundle.min.js',
    'bootstrap-icons/LICENSE', 'bootstrap-icons/bootstrap-icons.min.css',
    'bootstrap-icons/fonts/bootstrap-icons.woff', 'bootstrap-icons/fonts/bootstrap-icons.woff2',
}


@app.get('/vendor/<path:rel>')
def web_vendor(rel):
    if rel not in _WEB_VENDOR_FILES:
        abort(404)
    return send_from_directory(os.path.join(BASE_DIR, 'static', 'vendor'), rel, max_age=604800)


@app.get('/favicon.ico')
def favicon():
    return redirect(url_for('static', filename='brand/app-icon-256.png'))


# ---- 注册子模块蓝图（三个系统共用同一登录会话与页面风格）----
from modules.assets import bp as assets_bp, migrate_assets

app.register_blueprint(assets_bp)
from modules.asset_scan import bp as asset_scan_bp
app.register_blueprint(asset_scan_bp)
from modules.asset_units import bp as asset_units_bp
app.register_blueprint(asset_units_bp)
from modules.asset_labels import bp as asset_labels_bp
app.register_blueprint(asset_labels_bp)
app.context_processor(lambda: {"assets_enabled": True, "asset_units_enabled": True})
app.register_blueprint(funds_bp, url_prefix="/funds")
app.register_blueprint(invoices_bp, url_prefix="/invoices")
app.register_blueprint(task_api_bp)
app.register_blueprint(task_catalogs_bp)
app.register_blueprint(member_profiles_bp)
app.register_blueprint(member_avatars_bp)
app.register_blueprint(task_attachments_bp)
app.register_blueprint(task_progress_bp)
app.register_blueprint(discussions_bp, url_prefix="/discussions")
app.register_blueprint(daily_projects_bp)
app.register_blueprint(task_alerts_bp)
app.register_blueprint(item_topics_bp)
app.register_blueprint(email_bp)
app.register_blueprint(workspace_settings_bp)
app.context_processor(theme_context)
init_security(app)


@app.context_processor
def inject_funds_reviewer():
    """各模板的待审核角标与实际权限一致。"""
    user = current_user()
    return {"can_review_funds": bool(user and not user["must_change_password"] and can_review(user))}

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
    session_version INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT
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
        if "deleted_at" not in cols:
            db.execute("ALTER TABLE members ADD COLUMN deleted_at TEXT")
        if "must_change_password" not in cols:
            db.execute("ALTER TABLE members ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
        count = db.execute("SELECT COUNT(*) AS c FROM members").fetchone()[0]
        if count == 0:
            initial_password = os.environ.get("PROGRESS_INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
            if not os.environ.get("PROGRESS_INITIAL_ADMIN_PASSWORD"):
                credentials_path = os.environ.get("PROGRESS_INITIAL_CREDENTIALS_PATH", os.path.join(BASE_DIR, "initial-admin-password.txt"))
                write_initial_credentials(credentials_path, initial_password)
                print("[初始化] 初始凭据已写入受限文件：" + credentials_path, flush=True)
                print("[初始化] 请私下交付管理员，首次改密后删除该文件。", flush=True)
            db.execute(
                "INSERT INTO members (name, username, password_hash, group_name, role, created_at, must_change_password) "
                "VALUES (?, ?, ?, ?, ?, ?, 1)",
                ("管理员", "admin", generate_password_hash(initial_password), "", "admin", now_str()),
            )
            db.commit()
        else:
            db.commit()
        migrate_tasks(db)
        migrate_workspace(db)
        migrate_avatars(db)
        migrate_daily(db)
        migrate_collaboration(db)
        migrate_item_topics(db)
        migrate_security(db)
        migrate_assets(db)
        migrate_invoice_owners(db)
        migrate_invoice_jobs(db)
        cleanup_audit_rows(db)
        # Hot read paths (dashboard tree, status filters, attachment lookups)
        # would otherwise scan the whole table on every 5s poll.
        db.executescript("""
            CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_id, id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, id);
            CREATE INDEX IF NOT EXISTS idx_tasks_deleted ON tasks(deleted_at, id);
            CREATE INDEX IF NOT EXISTS idx_task_attachments_task ON task_attachments(task_id, id);
            CREATE INDEX IF NOT EXISTS idx_logs_task ON logs(task_id, id);
        """)
        db.commit()
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
_LOGIN_IP_LIMIT = int(os.environ.get("LOGIN_IP_FAILURE_LIMIT", "0"))
_LOGIN_GLOBAL_LIMIT = int(os.environ.get("LOGIN_GLOBAL_FAILURE_LIMIT", "0"))


def _login_attempt_key(username):
    """账号独立计数，不依赖共享代理地址或可伪造的转发头。"""
    raw = username.casefold()
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _login_counter_keys(username):
    ip_hash = hashlib.sha256(client_address().encode('utf-8')).hexdigest()
    return (_login_attempt_key(username), 'ip:' + ip_hash, 'global:')


def _retry_login(seconds):
    response = jsonify(ok=False, error='尝试次数过多，请稍后重试')
    response.status_code = 429
    response.headers['Retry-After'] = str(max(1, int(seconds)))
    return response


def _login_gate(db, keys, now):
    # The probe reservation lives in SQLite, so threads/workers share the budget.
    db.execute('BEGIN IMMEDIATE')
    try:
        rows = {row['attempt_key']: row for row in db.execute(
            'SELECT * FROM login_attempts WHERE attempt_key IN (?,?,?)', keys)}
        wait = max([0] + [rows[key]['locked_until'] - now for key in (keys[:2] if _LOGIN_IP_LIMIT > 0 else keys[:1]) if key in rows])
        if wait > 0:
            db.commit()
            return wait
        global_row = rows.get('global:')
        if _LOGIN_GLOBAL_LIMIT > 0 and global_row and global_row['locked_until'] > now:
            probe = db.execute("SELECT locked_until FROM login_attempts WHERE attempt_key='global:probe'").fetchone()
            if probe and probe['locked_until'] > now:
                db.commit()
                return 1
            db.execute("INSERT INTO login_attempts VALUES('global:probe',0,?,?) ON CONFLICT(attempt_key) DO UPDATE SET first_failed_at=excluded.first_failed_at,locked_until=excluded.locked_until", (now, now + 1))
        db.commit()
        return 0
    except Exception:
        db.rollback()
        raise


def _failed_login(db, keys, now):
    # Read and increment all three counters in one short write transaction.
    db.execute('BEGIN IMMEDIATE')
    try:
        waits = []
        for key, limit in zip(keys, (_LOGIN_FAILURE_LIMIT, _LOGIN_IP_LIMIT, _LOGIN_GLOBAL_LIMIT)):
            if limit <= 0:
                continue
            row = db.execute('SELECT * FROM login_attempts WHERE attempt_key=?', (key,)).fetchone()
            if row and (now - row['first_failed_at'] < _LOGIN_WINDOW or row['locked_until'] > now):
                failures, first = row['failures'] + 1, row['first_failed_at']
            else:
                failures, first = 1, now
            locked_until = (row['locked_until'] if row and row['locked_until'] > now else now + _LOGIN_WINDOW) if failures >= limit else 0
            db.execute('INSERT INTO login_attempts VALUES(?,?,?,?) ON CONFLICT(attempt_key) DO UPDATE SET failures=excluded.failures,first_failed_at=excluded.first_failed_at,locked_until=excluded.locked_until',
                       (key, failures, first, locked_until))
            if locked_until > now:
                waits.append(1 if key == 'global:' else locked_until - now)
        db.execute('DELETE FROM login_attempts WHERE first_failed_at<? AND locked_until<=?', (now - 2 * _LOGIN_WINDOW, now))
        db.commit()
        return max(waits, default=0)
    except Exception:
        db.rollback()
        raise


def task_login_destination(candidate):
    """Only supported local task links can survive the login round trip."""
    if not isinstance(candidate, str):
        return '/'
    if re.fullmatch(r'/assets(?:/detail\?id=[1-9]\d{0,18}|\?view=mine)?', candidate):
        return candidate
    old_daily = re.fullmatch(r'/daily-projects(?:/([1-9]\d{0,18}))?', candidate)
    if old_daily:
        return '/tasks?type=daily' + ('&project=' + old_daily[1] if old_daily[1] else '')
    if re.fullmatch(r'/tasks(?:\?task=[1-9]\d{0,18}|\?type=vehicle(?:&task=[1-9]\d{0,18})?|\?type=daily(?:&project=[1-9]\d{0,18})?)?', candidate):
        return candidate
    return '/'


@app.route("/login", methods=["GET", "POST"])
def login():
    candidate = request.args.get('next', '') if request.method == 'GET' else (request.get_json(silent=True) or request.form).get('next', '')
    destination = task_login_destination(candidate)
    if request.method == "GET":
        if current_user():
            return redirect(destination)
        return render_template("login.html", me=None, page="login", role_names=ROLE_NAMES)
    data = request.get_json(silent=True) or request.form
    return authenticate_login(data)


def authenticate_login(data):
    """One credential/rate-limit/session path for the website and native apps."""
    username = data.get("username") or ""
    password = data.get("password", "")
    if (not isinstance(username, str) or not isinstance(password, str)
            or len(username) > 64 or len(password) > 1024):
        return jsonify(ok=False, error="用户名或密码格式不正确", reason="invalid_payload"), 400
    username = username.strip()
    destination = task_login_destination(data.get("next", ""))
    db = get_db()
    keys = _login_counter_keys(username)
    now = int(time.time())
    wait = _login_gate(db, keys, now)
    if wait:
        return _retry_login(wait)
    row = db.execute(
        "SELECT * FROM members WHERE username = ? AND is_active = 1 AND deleted_at IS NULL", (username,)
    ).fetchone()
    if not valid_password(password) or row is None or not check_password_hash(row["password_hash"], password):
        locked = _failed_login(db, keys, now)
        if locked:
            return _retry_login(locked)
        return jsonify(ok=False, error="用户名或密码错误"), 400
    db.execute("DELETE FROM login_attempts WHERE attempt_key=?", (keys[0],))
    db.commit()
    session.clear()
    session.permanent = True
    session["uid"] = row["id"]
    session["session_version"] = row["session_version"]
    return jsonify(ok=True, must_change_password=bool(row["must_change_password"]),
                   redirect="/account" if row["must_change_password"] else destination)


@app.route("/logout", methods=["POST"])
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
    task_type = 'daily' if request.args.get('type') == 'daily' else 'vehicle'
    project_id = request.args.get('project', '')
    project_id = project_id if re.fullmatch(r'[1-9]\d{0,18}', project_id) else ''
    return render_template("board.html", me=current_user(), page="tasks", role_names=ROLE_NAMES,
                           statuses=STATUSES, priorities=PRIORITIES, task_type=task_type,
                           project_id=project_id if task_type == 'daily' else '')


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
    row = db.execute("SELECT id, role, deleted_at, is_active, session_version FROM members WHERE id = ?", (member_id,)).fetchone()
    if row is None:
        return jsonify(ok=False, error="成员不存在"), 404
    if row["deleted_at"] is not None:
        return jsonify(ok=False, error="请先恢复已删除成员"), 409
    if member_id == user["id"]:
        return jsonify(ok=False, error="修改本人密码请验证原密码"), 400
    if user["role"] != "admin" and row["role"] != "member":
        return jsonify(ok=False, error="无权限"), 403
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请求必须为 JSON 对象"), 400
    generated_password = data.get("password") in (None, "")
    password = secrets.token_urlsafe(10) if generated_password else data['password']
    if not valid_password(password):
        return jsonify(ok=False, error="密码必须为 8 至 1024 位"), 400
    password_hash = generate_password_hash(password)
    db.execute('BEGIN IMMEDIATE')
    try:
        actor = current_user()
        if actor is None or actor['must_change_password']:
            db.rollback()
            return jsonify(ok=False, error="登录状态已变化，请重新登录"), 401
        target = db.execute('SELECT role,deleted_at,is_active,session_version FROM members WHERE id=?', (member_id,)).fetchone()
        if actor['role'] not in ('admin', 'manager') or (actor['role'] != 'admin' and target and target['role'] != 'member'):
            db.rollback()
            return jsonify(ok=False, error="权限已变化，不能重置此账号"), 403
        if target is None or any(target[key] != row[key] for key in ('role', 'deleted_at', 'is_active', 'session_version')):
            db.rollback()
            return jsonify(ok=False, error="账号状态已变化，请重新核对后操作"), 409
        db.execute("UPDATE members SET password_hash = ?, must_change_password=1, session_version = session_version + 1 WHERE id = ?",
                   (password_hash, member_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify(ok=True, initial_password=password if generated_password else None)


@app.route("/api/member/<int:member_id>/role", methods=["POST"])
@manager_required
def api_member_role(member_id):
    user = current_user()
    if user["role"] != "admin":
        return jsonify(ok=False, error="仅超级管理员可调整角色"), 403
    if member_id == user["id"]:
        return jsonify(ok=False, error="不能修改自己的角色"), 400
    data = request.get_json(silent=True) or {}
    role = data.get("role") if isinstance(data, dict) else None
    if not isinstance(role, str) or role not in ROLE_NAMES:
        return jsonify(ok=False, error="角色不合法"), 400
    db = get_db()
    row = db.execute("SELECT id, deleted_at FROM members WHERE id = ?", (member_id,)).fetchone()
    if row is None:
        return jsonify(ok=False, error="成员不存在"), 404
    if row["deleted_at"] is not None:
        return jsonify(ok=False, error="请先恢复已删除成员"), 409
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
    row = db.execute("SELECT id, role, is_active, deleted_at FROM members WHERE id = ?", (member_id,)).fetchone()
    if row is None:
        return jsonify(ok=False, error="成员不存在"), 404
    if user["role"] != "admin" and row["role"] != "member":
        return jsonify(ok=False, error="无权限"), 403
    if row["deleted_at"] is not None:
        return jsonify(ok=False, error="请先恢复已删除成员"), 409
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
    if not isinstance(old_password, str) or len(old_password) > 1024 or not valid_password(new_password):
        return jsonify(ok=False, error="新密码必须为 8 至 1024 位"), 400
    db = get_db()
    row = db.execute("SELECT password_hash FROM members WHERE id = ?", (user["id"],)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], old_password):
        return jsonify(ok=False, error="原密码错误"), 400
    if check_password_hash(row["password_hash"], new_password):
        return jsonify(ok=False, error="新密码不能与当前密码相同"), 400
    db.execute("UPDATE members SET password_hash = ?, must_change_password=0, session_version = session_version + 1 WHERE id = ?",
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
app.register_blueprint(create_client_blueprint(authenticate_login, STATUSES, PRIORITIES))
app.register_blueprint(client_updates_bp)
app.register_blueprint(site_updates_bp)
init_db()

# ---- 本地调试入口（线上由 systemd 调用 gunicorn，不会走到这里）----
if __name__ == "__main__":
    start_invoice_worker()
    app.run(host="0.0.0.0", port=PORT)
