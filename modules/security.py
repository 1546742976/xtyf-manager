"""Session CSRF, first-login gate, stable-ID permissions and request audit trail."""
import json
import os
import secrets
import sqlite3
import subprocess
from contextlib import closing
from functools import wraps

from flask import (Blueprint, current_app, g, jsonify, redirect, render_template,
                   request, session, url_for)

from common import DB_PATH, ROLE_NAMES, current_user, get_db, login_required, now_str

bp = Blueprint("security", __name__)
PERMISSIONS = {"funds.review": "资金审核", "invoices.manage": "发票管理", "assets.manage": "资产管理"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
AUDITED_READS = {"funds.export_csv", "invoices.api_export_download", "invoices.api_export_zip"}
AUDIT_FIELDS = {"id", "ids", "member_id", "record_type", "amount", "invoice_count",
                "record_date", "status", "submitter_id", "permissions", "role",
                "is_active", "type", "job_type", "group_ids", "project_ids"}


def write_initial_credentials(path, password):
    """Create an owner-only bootstrap file before writing any secret bytes."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        if os.name == "nt":
            import csv
            identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                      check=True, capture_output=True, text=True)
            sid = next(csv.reader(identity.stdout.strip().splitlines()))[1]
            if not sid.startswith("S-1-"):
                raise RuntimeError("无法确认初始凭据文件的所有者")
            subprocess.run(["icacls", path, "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
                           check=True, capture_output=True)
        stream.write("admin\n" + password + "\n")


def migrate_security(db):
    """Only bootstrap legacy grants once; later renames never confer authority."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS security_migrations (version INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS member_permissions (
            member_id INTEGER NOT NULL REFERENCES members(id),
            permission TEXT NOT NULL,
            granted_by INTEGER,
            granted_at TEXT NOT NULL,
            PRIMARY KEY(member_id, permission)
        );
        CREATE TABLE IF NOT EXISTS security_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER, actor_name TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL, method TEXT NOT NULL, target TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}',
            outcome TEXT NOT NULL DEFAULT 'pending', http_status INTEGER,
            created_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_security_audit_actor ON security_audit(actor_id,id);
    """)
    db.execute("BEGIN IMMEDIATE")
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(members)")}
        if "must_change_password" not in cols:
            db.execute("ALTER TABLE members ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
        if not db.execute("SELECT 1 FROM security_migrations WHERE version=1").fetchone():
            reviewers = {name.strip() for name in os.environ.get("FUNDS_REVIEWERS", "chy").split(",") if name.strip()}
            for member_id, username in db.execute(
                "SELECT id,username FROM members WHERE role IN ('admin','manager') "
                "AND is_active=1 AND deleted_at IS NULL"
            ).fetchall():
                permissions = ["invoices.manage"]
                if username in reviewers:
                    permissions.append("funds.review")
                for permission in permissions:
                    db.execute("INSERT OR IGNORE INTO member_permissions VALUES(?,?,NULL,?)",
                               (member_id, permission, now_str()))
            db.execute("INSERT INTO security_migrations VALUES(1)")
        db.commit()
    except Exception:
        db.rollback()
        raise


def has_permission(permission, user=None):
    user = user or current_user()
    return bool(user and get_db().execute(
        "SELECT 1 FROM member_permissions WHERE member_id=? AND permission=?",
        (user["id"], permission),
    ).fetchone())


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user()["role"] != "admin":
            return jsonify(ok=False, error="仅管理员可访问"), 403
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _error(message, reason, status=403):
    return jsonify(ok=False, success=False, code=status, error=message, msg=message,
                   reason=reason), status


def _audit_details(data):
    if not isinstance(data, dict):
        return {}
    # Never persist passwords, tokens, file bytes, free-text notes or import rows.
    result = {key: value for key, value in data.items() if key in AUDIT_FIELDS}
    if isinstance(data.get("updates"), dict):
        result["updates"] = _audit_details(data["updates"])
    if isinstance(data.get("items"), list):
        result["item_count"] = len(data["items"])
        result["items"] = [_audit_details(item) for item in data["items"][:200]]
    return result


def audit_background_start(job_type, job_id, db=None):
    if db is None:
        connection = sqlite3.connect(DB_PATH, timeout=15)
        try:
            with connection:
                return audit_background_start(job_type, job_id, db=connection)
        finally:
            connection.close()
    user = current_user()
    return db.execute(
        "INSERT INTO security_audit(actor_id,actor_name,action,method,target,details,created_at) VALUES(?,?,?,?,?,?,?)",
        (user["id"], user["name"], "invoices.job." + job_type, "JOB", job_id,
         json.dumps({"request_audit_id": getattr(g, "audit_id", None)}), now_str()),
    ).lastrowid


def audit_background_finish(audit_id, outcome, result, db=None):
    if db is None:
        connection = sqlite3.connect(DB_PATH, timeout=15)
        try:
            with connection:
                return audit_background_finish(audit_id, outcome, result, db=connection)
        finally:
            connection.close()
    prior = db.execute("SELECT details FROM security_audit WHERE id=?", (audit_id,)).fetchone()
    details = json.loads(prior[0]) if prior else {}
    details["result"] = result
    db.execute("UPDATE security_audit SET outcome=?,details=?,finished_at=? WHERE id=?",
               (outcome, json.dumps(details, ensure_ascii=False), now_str(), audit_id))


def init_security(app):
    app.register_blueprint(bp)
    app.context_processor(lambda: {"csrf_token": csrf_token,
                                    "can_manage_invoices": has_permission("invoices.manage")})

    @app.before_request
    def protect_request():
        # Bound asset writes before the shared audit logger parses their JSON.
        # Support Flask 3.0 too (per-request max_content_length became writable in 3.1).
        if request.path.startswith('/api/assets/') and request.method not in SAFE_METHODS:
            limit = 16 * 1024
            if request.content_length is not None and request.content_length > limit:
                return jsonify(ok=False, error="资产请求体超过 16 KiB 限制"), 413
            if request.content_length is None:
                raw = request.stream.read(limit + 1)
                if len(raw) > limit:
                    return jsonify(ok=False, error="资产请求体超过 16 KiB 限制"), 413
                request._cached_data = raw  # Retain the bounded body for get_json/audit.
        user = current_user() if request.endpoint != "static" else None
        unsafe = request.method not in SAFE_METHODS
        if unsafe or request.endpoint in AUDITED_READS:
            details = _audit_details(request.get_json(silent=True)) if request.is_json else {}
            g.audit_details = details
            with closing(sqlite3.connect(DB_PATH, timeout=15)) as db, db:
                g.audit_id = db.execute(
                    "INSERT INTO security_audit(actor_id,actor_name,action,method,target,details,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (user["id"] if user else None, user["name"] if user else "",
                     request.endpoint or "unknown", request.method, request.path,
                     json.dumps(details, ensure_ascii=False)[:20000], now_str()),
                ).lastrowid
        if unsafe:
            expected = session.get("csrf_token")
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
            if (request.headers.get("Sec-Fetch-Site") == "cross-site" or
                    not isinstance(expected, str) or not isinstance(supplied, str) or
                    not secrets.compare_digest(expected.encode(), supplied.encode())):
                return _error("页面凭据已过期，请刷新页面后重试", "csrf_failed")
            if request.is_json and not isinstance(request.get_json(silent=True), dict):
                return _error("请求必须是 JSON 对象", "invalid_payload", 400)
        native_client = request.headers.get("X-XTYF-Client-Protocol") == "1"
        if native_client and user is None and request.endpoint not in {
            "client_api.bootstrap", "client_api.login", "client_api.logout", "security.get_csrf",
            "client_updates.updates", "client_updates.bundle_download",
        }:
            return _error("未登录", "authentication_required", 401)
        if user and user["must_change_password"] and request.endpoint not in {
            "static", "account", "api_password", "logout", "security.get_csrf",
            "client_api.bootstrap", "client_api.logout",
            "client_updates.updates", "client_updates.bundle_download",
        }:
            if native_client or "/api/" in request.path:
                return _error("请先在账户设置中修改初始密码", "password_change_required")
            return redirect(url_for("account"))

    @app.after_request
    def finish_request(response):
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-CSRF-Token"] = csrf_token()
            # Inline handlers are still used by the finance pages, so this policy
            # deliberately only locks down framing, plugins and <base> injection.
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            response.headers.setdefault("Referrer-Policy", "same-origin")
            response.headers.setdefault(
                "Content-Security-Policy",
                "frame-ancestors 'self'; object-src 'none'; base-uri 'self'",
            )
        audit_id = getattr(g, "audit_id", None)
        if audit_id:
            payload = response.get_json(silent=True) if response.is_json else None
            outcome = "success" if response.status_code < 400 else "failure"
            if isinstance(payload, dict):
                if (payload.get("ok") is False or payload.get("success") is False or
                        payload.get("code", 0) not in (0, None)):
                    outcome = "failure"
                if isinstance(payload.get("results"), list) and any(
                    item.get("status") == "error" for item in payload["results"] if isinstance(item, dict)
                ):
                    outcome = "partial"
                summary = {key: payload[key] for key in ("id", "topic_id", "message_id", "rel_path", "job_id", "count", "moved", "review_required", "amount", "amount_cents") if key in payload}
                if isinstance(payload.get("created"), list):
                    summary["created_count"] = len(payload["created"])
                if summary:
                    g.audit_details["result"] = summary
            # A crash or failure to finalize leaves the durable 'pending' record visible.
            try:
                with closing(sqlite3.connect(DB_PATH, timeout=5)) as db, db:
                    db.execute("UPDATE security_audit SET outcome=?,http_status=?,finished_at=?,details=? WHERE id=?",
                               (outcome, response.status_code, now_str(), json.dumps(g.audit_details, ensure_ascii=False)[:20000], audit_id))
            except sqlite3.Error:
                current_app.logger.exception("Could not finalize audit record %s", audit_id)
        return response


@bp.get("/api/csrf")
def get_csrf():
    return jsonify(ok=True, csrf_token=csrf_token())


@bp.get("/admin/security")
@admin_required
def security_page():
    return render_template("security.html", me=current_user(), page="security", role_names=ROLE_NAMES)


@bp.get("/api/admin/permissions")
@admin_required
def permissions_get():
    db = get_db()
    members = [dict(row) for row in db.execute(
        "SELECT id,name,username,role,is_active,deleted_at FROM members ORDER BY id")]
    grants = {}
    for row in db.execute("SELECT member_id,permission FROM member_permissions ORDER BY permission"):
        grants.setdefault(row["member_id"], []).append(row["permission"])
    return jsonify(ok=True, permissions=PERMISSIONS,
                   members=[member | {"permissions": grants.get(member["id"], [])} for member in members])


@bp.post("/api/admin/permissions/<int:member_id>")
@admin_required
def permissions_set(member_id):
    if member_id <= 0 or member_id > 2**63 - 1:
        return jsonify(ok=False, error="成员 ID 不合法"), 400
    data = request.get_json(silent=True)
    values = data.get("permissions") if isinstance(data, dict) else None
    if (not isinstance(values, list) or any(not isinstance(p, str) or p not in PERMISSIONS for p in values)
            or len(values) != len(set(values))):
        return jsonify(ok=False, error="权限列表不合法"), 400
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT role,is_active,deleted_at FROM members WHERE id=?", (member_id,)).fetchone()
    if row is None:
        db.rollback()
        return jsonify(ok=False, error="成员不存在"), 404
    if values and (not row["is_active"] or row["deleted_at"] is not None):
        db.rollback()
        return jsonify(ok=False, error="停用或已删除账号只能撤销权限"), 400
    if "funds.review" in values and row["role"] not in ("admin", "manager"):
        db.rollback()
        return jsonify(ok=False, error="资金审核人必须具备资金工作区访问角色（管理员或项管）"), 400
    db.execute("DELETE FROM member_permissions WHERE member_id=?", (member_id,))
    db.executemany("INSERT INTO member_permissions VALUES(?,?,?,?)",
                   [(member_id, p, current_user()["id"], now_str()) for p in values])
    db.commit()
    return jsonify(ok=True)


@bp.get("/api/admin/audit")
@admin_required
def audit_get():
    try:
        before = int(request.args.get("before", 0))
        if before < 0 or before > 2**63 - 1:
            raise ValueError()
    except ValueError:
        return jsonify(ok=False, error="分页参数不合法"), 400
    rows = [dict(row) for row in get_db().execute(
        "SELECT * FROM security_audit WHERE (?=0 OR id<?) ORDER BY id DESC LIMIT 51", (before, before))]
    return jsonify(ok=True, events=rows[:50], next_before=rows[49]["id"] if len(rows) > 50 else None)
