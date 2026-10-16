"""Account appearance preferences and the workspace's single target date."""
from datetime import date, datetime, timedelta, timezone
import re

from flask import Blueprint, g, jsonify, request

from common import current_user, get_db, login_required, now_str

bp = Blueprint("workspace_settings", __name__)
LIGHT_THEMES = frozenset({"silver", "rm-light", "mist", "paw"})
DARK_THEMES = frozenset({"space", "rm-dark", "graphite", "xueli"})
THEME_MODES = frozenset({"light", "dark", "system"})
BEIJING = timezone(timedelta(hours=8))


def migrate(db):
    """Keep the legacy two-value column and preserve existing dark accounts."""
    db.execute("BEGIN IMMEDIATE")
    try:
        columns = {row[1] for row in db.execute("PRAGMA table_info(members)")}
        if "theme_mode" not in columns:
            db.execute("ALTER TABLE members ADD COLUMN theme_mode TEXT NOT NULL DEFAULT 'light' "
                       "CHECK(theme_mode IN ('light','dark','system'))")
            db.execute("UPDATE members SET theme_mode=ui_theme")
        if "light_theme" not in columns:
            db.execute("ALTER TABLE members ADD COLUMN light_theme TEXT NOT NULL DEFAULT 'silver' "
                       "CHECK(light_theme IN ('silver','rm-light','mist','paw'))")
        if "dark_theme" not in columns:
            db.execute("ALTER TABLE members ADD COLUMN dark_theme TEXT NOT NULL DEFAULT 'space' "
                       "CHECK(dark_theme IN ('space','rm-dark','graphite','xueli'))")
        db.execute("""CREATE TABLE IF NOT EXISTS workspace_countdown (
            id INTEGER PRIMARY KEY CHECK(id=1),
            title TEXT NOT NULL DEFAULT '', target_date TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
            updated_by INTEGER REFERENCES members(id), updated_at TEXT NOT NULL DEFAULT ''
        )""")
        db.execute("INSERT OR IGNORE INTO workspace_countdown(id) VALUES(1)")
        db.commit()
    except Exception:
        db.rollback()
        raise


def preferences(user=None):
    if user is None:
        return {"theme": "light", "mode": "light", "light_theme": "silver", "dark_theme": "space"}
    return {"theme": user["ui_theme"], "mode": user["theme_mode"],
            "light_theme": user["light_theme"], "dark_theme": user["dark_theme"]}


def theme_context():
    return {"theme_preferences": preferences(current_user())}


def beijing_today():
    return datetime.now(BEIJING).date()


def countdown_payload(row, today=None):
    today = today or beijing_today()
    configured = bool(row["title"] and row["target_date"])
    return {"title": row["title"], "target_date": row["target_date"],
            "version": row["version"], "configured": configured,
            "today": today.isoformat(),
            "days_left": (date.fromisoformat(row["target_date"]) - today).days if configured else None}


@bp.route("/api/workspace/countdown", methods=["GET", "PUT"])
@login_required
def workspace_countdown():
    db = get_db()
    if request.method == "GET":
        return jsonify(ok=True, countdown=countdown_payload(
            db.execute("SELECT * FROM workspace_countdown WHERE id=1").fetchone()))
    actor = current_user()
    if actor["role"] != "admin":
        return jsonify(ok=False, error="仅管理员可修改目标倒计时"), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"title", "target_date", "version"}:
        return jsonify(ok=False, error="请提交目标名称、日期及版本"), 400
    title, target, version = data["title"], data["target_date"], data["version"]
    if (not isinstance(title, str) or not isinstance(target, str)
            or type(version) is not int or not 1 <= version <= 9223372036854775807):
        return jsonify(ok=False, error="目标名称、日期或版本格式不正确"), 400
    title = title.strip()
    if len(title) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in title):
        return jsonify(ok=False, error="目标名称最长 80 字且不能含控制字符"), 400
    if bool(title) != bool(target):
        return jsonify(ok=False, error="请同时填写目标名称和日期，或同时清空以隐藏横幅"), 400
    if target:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target, flags=re.ASCII):
                raise ValueError()
            date.fromisoformat(target)
        except ValueError:
            return jsonify(ok=False, error="请选择有效的目标日期"), 400
    db.execute("BEGIN IMMEDIATE")
    try:
        previous = db.execute("SELECT * FROM workspace_countdown WHERE id=1").fetchone()
        if previous["version"] != version:
            db.rollback()
            return jsonify(ok=False, error="目标已由其他管理员修改，请刷新后重新确认",
                           countdown=countdown_payload(previous)), 409
        db.execute("UPDATE workspace_countdown SET title=?,target_date=?,version=version+1,"
                   "updated_by=?,updated_at=? WHERE id=1", (title, target, actor["id"], now_str()))
        row = db.execute("SELECT * FROM workspace_countdown WHERE id=1").fetchone()
        db.commit()
    except Exception:
        db.rollback()
        raise
    # The common request audit records actor, endpoint and success/failure. Add
    # changed configuration metadata without logging free-text user content.
    if hasattr(g, "audit_details"):
        g.audit_details["countdown"] = {
            "previous_date": previous["target_date"], "target_date": target,
            "previous_version": version, "version": row["version"],
            "title_changed": previous["title"] != title, "configured": bool(target),
        }
    return jsonify(ok=True, countdown=countdown_payload(row))
