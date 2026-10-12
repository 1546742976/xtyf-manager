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

# ---- 关键路径与端口（secret.key 用于会话签名，首次运行自动生成）----
SECRET_PATH = os.path.join(BASE_DIR, "secret.key")
PORT = int(os.environ.get("PORT", "8520"))

# ---- 全局常量：任务状态枚举（看板/表单/校验共用）----
STATUSES = ["未开始", "进行中", "已完成", "阻塞", "已取消"]
# ---- 任务优先级（看板上以彩色圆点显示）----
PRIORITIES = ["高", "中", "低"]
# ---- 系统角色：admin=管理员 / manager=项管 / member=成员 ----
ROLE_NAMES = {"admin": "管理员", "manager": "项管", "member": "成员"}
# ---- 字段中文名（任务更新日志里显示"字段: 旧值 → 新值"）----
FIELD_NAMES = {
    "title": "标题",
    "detail": "说明",
    "owner_id": "负责人",
    "group_name": "组别",
    "project": "项目",
    "priority": "优先级",
    "status": "状态",
    "start_date": "开始日期",
    "due_date": "截止日期",
}
# ---- 项管/管理员可编辑的任务字段白名单（状态单独校验）----
MANAGER_FIELDS = ("title", "detail", "owner_id", "group_name", "project",
                  "priority", "start_date", "due_date")


# ---- 工具：安全转整数并夹取上下限（表单输入容错）----
def parse_int(value, default=0, lo=None, hi=None):
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if lo is not None:
        result = max(lo, result)
    if hi is not None:
        result = min(hi, result)
    return result


def validate_task_dates(start_date, due_date):
    """合法日期必须符合 YYYY-MM-DD；允许留空。"""
    values = []
    for label, raw in (("开始日期", start_date), ("截止日期", due_date)):
        if raw is not None and not isinstance(raw, str):
            return f"{label}格式不正确"
        value = (raw or "").strip()
        if value:
            try:
                values.append(datetime.strptime(value, "%Y-%m-%d").date())
            except ValueError:
                return f"{label}格式不正确（应为 YYYY-MM-DD）"
        else:
            values.append(None)
    if values[0] and values[1] and values[0] > values[1]:
        return "开始日期不能晚于截止日期"
    return None


def validate_task_owner(db, owner_id):
    if owner_id is None:
        return None
    row = db.execute("SELECT id FROM members WHERE id = ? AND is_active = 1", (owner_id,)).fetchone()
    return None if row else "负责人不存在或已停用"


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

# ---- 注册子模块蓝图（三个系统共用同一登录会话与页面风格）----
app.register_blueprint(funds_bp, url_prefix="/funds")
app.register_blueprint(invoices_bp, url_prefix="/invoices")


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
    created_at TEXT NOT NULL
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
        count = db.execute("SELECT COUNT(*) AS c FROM members").fetchone()[0]
        if count == 0:
            initial_password = os.environ.get("PROGRESS_INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
            if not os.environ.get("PROGRESS_INITIAL_ADMIN_PASSWORD"):
                print("[初始化] admin 初始随机密码：" + initial_password, flush=True)
                print("[初始化] 请登录后修改密码并保护服务器日志。", flush=True)
            db.execute(
                "INSERT INTO members (name, username, password_hash, group_name, role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("管理员", "admin", generate_password_hash(initial_password), "管理员", "admin", now_str()),
            )
            db.commit()
    finally:
        db.close()


# ---- 辅助：ID → 成员姓名（用于日志/变更记录的人类可读显示）----
def member_name(db, member_id):
    if not member_id:
        return "未分配"
    row = db.execute("SELECT name FROM members WHERE id = ?", (member_id,)).fetchone()
    return row["name"] if row else "未分配"


# ---- 响应头：API 与主页禁止缓存，保证看板"5 秒自动刷新"体验 ----
@app.after_request
def add_no_cache(resp):
    if request.path.startswith("/api/") or request.path == "/":
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ==================== 登录 / 登出 ====================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if current_user():
            return redirect(url_for("index"))
        return render_template("login.html", me=None, page="login", role_names=ROLE_NAMES)
    data = request.get_json(silent=True) or request.form
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    row = get_db().execute(
        "SELECT * FROM members WHERE username = ? AND is_active = 1", (username,)
    ).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password):
        return jsonify(ok=False, error="用户名或密码错误"), 400
    session.clear()
    session.permanent = True
    session["uid"] = row["id"]
    return jsonify(ok=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ==================== 页面：看板（/）与任务管理（/manage）====================
@app.route("/")
@login_required
def index():
    return render_template("board.html", me=current_user(), page="board", role_names=ROLE_NAMES)


@app.route("/account")
@login_required
def account():
    """所有登录角色的账户设置页面，统一各模块的密码修改入口。"""
    return render_template("account.html", me=current_user(),
                           page="account", role_names=ROLE_NAMES)


@app.route("/manage")
@login_required
def manage():
    user = current_user()
    if user["role"] not in ("admin", "manager"):
        return redirect(url_for("index"))
    return render_template("manage.html", me=user, page="manage", role_names=ROLE_NAMES,
                           statuses=STATUSES, priorities=PRIORITIES)


# ==================== 看板数据 API ====================
@app.route("/api/data")
@login_required
def api_data():
    db = get_db()
    members = [dict(r) for r in db.execute(
        "SELECT id, name, group_name, role FROM members WHERE is_active = 1 ORDER BY group_name, id"
    ).fetchall()]
    tasks = [dict(r) for r in db.execute(
        "SELECT * FROM tasks ORDER BY CASE WHEN due_date = '' THEN 1 ELSE 0 END, due_date, id DESC"
    ).fetchall()]
    # 剩余天数 = 截止日期 - 今天（服务器时区已是北京时间，每天自动变化）
    # 前端按 days_left 显示：>3 蓝 / <=3 橙 / <0 红 / 无截止灰
    today = datetime.now().date()
    for task in tasks:
        due = (task.get("due_date") or "").strip()
        if due:
            try:
                task["days_left"] = (datetime.strptime(due, "%Y-%m-%d").date() - today).days
            except ValueError:
                task["days_left"] = None
        else:
            task["days_left"] = None
    return jsonify(ok=True, me=current_user(), members=members, tasks=tasks,
                   statuses=STATUSES, priorities=PRIORITIES)


# ==================== 成员管理 API（manager+）====================
@app.route("/api/members")
@manager_required
def api_members():
    rows = get_db().execute(
        "SELECT id, name, username, group_name, role, is_active FROM members ORDER BY group_name, id"
    ).fetchall()
    return jsonify(ok=True, members=[dict(r) for r in rows])


# ==================== 任务 API（新建 / 更新 / 删除 / 历史）====================
@app.route("/api/task", methods=["POST"])
@manager_required
def api_task_create():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify(ok=False, error="任务标题不能为空"), 400
    owner_id = parse_int(data.get("owner_id"), 0) or None
    group_name = (data.get("group_name") or "").strip()
    db = get_db()
    if data.get("owner_id") not in (None, "", 0, "0") and not owner_id:
        return jsonify(ok=False, error="负责人 ID 不合法"), 400
    err = validate_task_owner(db, owner_id) or validate_task_dates(data.get("start_date"), data.get("due_date"))
    if err:
        return jsonify(ok=False, error=err), 400
    if owner_id and not group_name:
        row = db.execute("SELECT group_name FROM members WHERE id = ?", (owner_id,)).fetchone()
        if row:
            group_name = row["group_name"] or ""
    status = data.get("status", "未开始")
    priority = data.get("priority", "中")
    if status not in STATUSES or priority not in PRIORITIES:
        return jsonify(ok=False, error="状态或优先级不合法"), 400
    cur = db.execute(
        "INSERT INTO tasks (title, detail, owner_id, group_name, project, priority, status, progress, "
        "start_date, due_date, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (title, (data.get("detail") or "").strip(), owner_id, group_name,
         (data.get("project") or "").strip(), priority, status,
         0,
         (data.get("start_date") or "").strip(), (data.get("due_date") or "").strip(),
         current_user()["id"], now_str(), now_str()),
    )
    db.commit()
    return jsonify(ok=True, id=cur.lastrowid)


# —— 更新任务：权限分级 —— 负责人本人只能改“状态/备注”；manager+ 可改全部字段
@app.route("/api/task/<int:task_id>", methods=["POST"])
@login_required
def api_task_update(task_id):
    user = current_user()
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return jsonify(ok=False, error="任务不存在"), 404
    data = request.get_json(silent=True) or {}
    is_manager = user["role"] in ("admin", "manager")
    is_owner = row["owner_id"] == user["id"]
    if not (is_manager or is_owner):
        return jsonify(ok=False, error="无权限"), 403

    old = dict(row)
    new_values = {}
    if is_manager:
        for key in MANAGER_FIELDS:
            if key in data:
                value = data[key]
                if key == "title":
                    value = (value or "").strip()
                    if not value:
                        return jsonify(ok=False, error="任务标题不能为空"), 400
                elif key == "owner_id":
                    value = parse_int(value, 0) or None
                elif isinstance(value, str):
                    value = value.strip()
                new_values[key] = value
    if is_manager:
        if "owner_id" in data and data["owner_id"] not in (None, "", 0, "0") and not new_values.get("owner_id"):
            return jsonify(ok=False, error="负责人 ID 不合法"), 400
        err = validate_task_owner(db, new_values.get("owner_id", old["owner_id"]))
        if err:
            return jsonify(ok=False, error=err), 400
        if new_values.get("priority", old["priority"]) not in PRIORITIES:
            return jsonify(ok=False, error="优先级不合法"), 400
        err = validate_task_dates(new_values.get("start_date", old["start_date"]),
                                  new_values.get("due_date", old["due_date"]))
        if err:
            return jsonify(ok=False, error=err), 400
    if "status" in data:
        status = data.get("status")
        if status not in STATUSES:
            return jsonify(ok=False, error="状态不合法"), 400
        new_values["status"] = status

    changes = []
    for key, value in new_values.items():
        old_value = old.get(key)
        if key == "owner_id":
            if old_value != value:
                changes.append(f"负责人: {member_name(db, old_value)} → {member_name(db, value)}")
        elif str(old_value) != str(value):
            changes.append(f"{FIELD_NAMES.get(key, key)}: {old_value} → {value}")

    note = (data.get("note") or "").strip()
    if not changes and not note:
        return jsonify(ok=True)

    if new_values:
        new_values["updated_at"] = now_str()
        sets = ", ".join(f"{key} = ?" for key in new_values)
        db.execute(f"UPDATE tasks SET {sets} WHERE id = ?", list(new_values.values()) + [task_id])
    db.execute(
        "INSERT INTO logs (task_id, member_id, member_name, note, changes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, user["id"], user["name"], note, "；".join(changes), now_str()),
    )
    db.commit()
    return jsonify(ok=True)


@app.route("/api/task/<int:task_id>/delete", methods=["POST"])
@manager_required
def api_task_delete(task_id):
    db = get_db()
    db.execute("DELETE FROM logs WHERE task_id = ?", (task_id,))
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    return jsonify(ok=True)


@app.route("/api/task/<int:task_id>/logs")
@login_required
def api_task_logs(task_id):
    rows = get_db().execute(
        "SELECT id, member_name, note, changes, created_at FROM logs WHERE task_id = ? ORDER BY id DESC LIMIT 100",
        (task_id,),
    ).fetchall()
    return jsonify(ok=True, logs=[dict(r) for r in rows])


# ==================== 成员管理：添加 / 批量导入 / 重置密码 / 角色 / 停用 ====================
@app.route("/api/member", methods=["POST"])
@manager_required
def api_member_create():
    user = current_user()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, error="姓名不能为空"), 400
    username = (data.get("username") or "").strip() or name
    generated_password = not bool(data.get("password"))
    password = data.get("password") or secrets.token_urlsafe(10)
    if len(password) < 8:
        return jsonify(ok=False, error="初始密码至少 8 位"), 400
    group_name = (data.get("group_name") or "").strip()
    role = data.get("role") or "member"
    if user["role"] != "admin" or role not in ("admin", "manager", "member"):
        role = "member"
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO members (name, username, password_hash, group_name, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, username, generate_password_hash(password), group_name, role, now_str()),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify(ok=False, error=f"用户名 {username} 已存在"), 400
    return jsonify(ok=True, id=cur.lastrowid,
                   initial_password=password if generated_password else None)


@app.route("/api/members/import", methods=["POST"])
@manager_required
def api_members_import():
    data = request.get_json(silent=True) or {}
    text = data.get("text") or ""
    db = get_db()
    existing = {r["username"] for r in db.execute("SELECT username FROM members").fetchall()}
    created = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.replace("，", ",").replace("\t", ",").split(",")]
        name = parts[0] if parts else ""
        if not name:
            continue
        group_name = parts[1] if len(parts) > 1 else ""
        username = parts[2] if len(parts) > 2 and parts[2] else name
        if username in existing:
            index = 2
            while f"{name}{index}" in existing:
                index += 1
            username = f"{name}{index}"
        password = secrets.token_urlsafe(9)
        db.execute(
            "INSERT INTO members (name, username, password_hash, group_name, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (name, username, generate_password_hash(password), group_name, "member", now_str()),
        )
        existing.add(username)
        created.append(f"{name}（{username} / {password}）")
    db.commit()
    return jsonify(ok=True, created=created)


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
    db.execute("UPDATE members SET password_hash = ? WHERE id = ?",
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
    db.execute("UPDATE members SET role = ? WHERE id = ?", (role, member_id))
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
    db.execute("UPDATE members SET is_active = ? WHERE id = ?",
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
    if len(new_password) < 6:
        return jsonify(ok=False, error="新密码至少 6 位"), 400
    db = get_db()
    row = db.execute("SELECT password_hash FROM members WHERE id = ?", (user["id"],)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], old_password):
        return jsonify(ok=False, error="原密码错误"), 400
    db.execute("UPDATE members SET password_hash = ? WHERE id = ?",
               (generate_password_hash(new_password), user["id"]))
    db.commit()
    return jsonify(ok=True)


# ==================== 导出任务 CSV（剩余天数为中文描述：剩余N天/已逾期N天/今天截止）====================
@app.route("/export.csv")
@login_required
def export_csv():
    rows = get_db().execute(
        "SELECT t.id, t.title, COALESCE(m.name, '未分配') AS owner, t.group_name, t.project, t.priority, "
        "t.status, t.progress, t.start_date, t.due_date, t.detail, t.updated_at "
        "FROM tasks t LEFT JOIN members m ON m.id = t.owner_id ORDER BY t.id"
    ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "任务", "负责人", "组别", "项目", "优先级", "状态", "剩余天数",
                     "开始日期", "截止日期", "说明", "更新时间"])
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
        writer.writerow([row["id"], row["title"], row["owner"], row["group_name"], row["project"],
                         row["priority"], row["status"], days_text, row["start_date"],
                         row["due_date"], row["detail"], row["updated_at"]])
    data = "\ufeff" + output.getvalue()
    filename = f"tasks-{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


# ---- 模块导入即初始化（幂等）：gunicorn 启动时自动完成建表与兜底账号 ----
init_db()

# ---- 本地调试入口（线上由 systemd 调用 gunicorn，不会走到这里）----
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
