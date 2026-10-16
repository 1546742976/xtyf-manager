"""Account profiles: stable member IDs, self-service fields and privileged edits.

The numeric members.id is never writable. Authorization and validation happen on
the server even when the management UI hides privileged fields.
"""
from __future__ import annotations

import sqlite3

from flask import Blueprint, jsonify, request, session
from werkzeug.security import generate_password_hash

from common import current_user, get_db, login_required
from modules.task_catalogs import _set_member_groups, _set_member_projects
from modules.task_validation import MAX_SQLITE_INTEGER, active_ids, normalize_ids
from modules.workspace_settings import DARK_THEMES, LIGHT_THEMES, THEME_MODES, preferences

bp = Blueprint("member_profiles", __name__)
SELF_FIELDS = frozenset({"username", "group_ids", "project_ids"})
ADMIN_FIELDS = SELF_FIELDS | {"name", "role", "is_active", "new_password"}
ROLES = frozenset({"member", "manager", "admin"})


def _profile(db: sqlite3.Connection, member_id: int) -> dict | None:
    row = db.execute(
        "SELECT id,name,username,role,is_active,deleted_at FROM members WHERE id=?", (member_id,)
    ).fetchone()
    if row is None:
        return None
    profile = dict(row)
    for key, table, column in (
        ("group_ids", "member_groups", "group_id"),
        ("project_ids", "member_projects", "project_id"),
    ):
        profile[key] = [int(item[0]) for item in db.execute(
            f"SELECT {column} FROM {table} WHERE member_id=? ORDER BY {column}",
            (member_id,),
        )]
    return profile


def _clean_text(value: object, label: str, max_length: int) -> tuple[str, str | None]:
    if not isinstance(value, str):
        return "", f"{label}必须是文本"
    cleaned = value.strip()
    if not cleaned or len(cleaned) > max_length or any(ord(c) < 32 or ord(c) == 127 for c in cleaned):
        return "", f"{label}不能为空、不能包含控制字符且最长 {max_length} 字"
    return cleaned, None


def _save(member_id: int, as_admin: bool):
    actor = current_user()
    if as_admin and actor["role"] != "admin":
        return jsonify(ok=False, error="仅管理员可修改其他成员资料"), 403
    if member_id <= 0 or member_id > MAX_SQLITE_INTEGER:
        return jsonify(ok=False, error="ID 格式不正确"), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请求格式不正确"), 400
    allowed = ADMIN_FIELDS if as_admin else SELF_FIELDS
    if not data or data.keys() - allowed:
        return jsonify(ok=False, error="包含空请求或不允许修改的字段"), 400

    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        target = db.execute(
            "SELECT id,name,username,role,is_active,deleted_at,session_version FROM members WHERE id=?",
            (member_id,),
        ).fetchone()
        if target is None:
            db.rollback()
            return jsonify(ok=False, error="成员不存在"), 404

        if target["deleted_at"] is not None:
            db.rollback()
            return jsonify(ok=False, error="已删除成员需先恢复才能编辑"), 409

        updates = {}
        for field, label, length in (("username", "用户名", 64), ("name", "姓名", 80)):
            if field in data:
                value, error = _clean_text(data[field], label, length)
                if error:
                    db.rollback()
                    return jsonify(ok=False, error=error), 400
                if value != target[field]:
                    updates[field] = value

        if as_admin:
            if "role" in data:
                role = data["role"]
                if not isinstance(role, str) or role not in ROLES:
                    db.rollback()
                    return jsonify(ok=False, error="角色不合法"), 400
                if role != target["role"]:
                    if member_id == actor["id"]:
                        db.rollback()
                        return jsonify(ok=False, error="不能修改自己的角色"), 400
                    updates["role"] = role

            if "is_active" in data:
                if type(data["is_active"]) is not bool:
                    db.rollback()
                    return jsonify(ok=False, error="启停状态必须是布尔值"), 400
                active = int(data["is_active"])
                if active != target["is_active"]:
                    if member_id == actor["id"]:
                        db.rollback()
                        return jsonify(ok=False, error="不能停用自己"), 400
                    updates["is_active"] = active

            if "new_password" in data:
                password = data["new_password"]
                if not isinstance(password, str) or (password and not 8 <= len(password) <= 1024):
                    db.rollback()
                    return jsonify(ok=False, error="密码必须为 8 至 1024 位"), 400
                if password:
                    if member_id == actor["id"]:
                        db.rollback()
                        return jsonify(ok=False, error="请在账户设置中验证原密码后修改本人密码"), 400
                    updates["password_hash"] = generate_password_hash(password)
                    updates["must_change_password"] = 1

        relations = {}
        for field, label, table in (
            ("group_ids", "专业组", "groups"),
            ("project_ids", "造车组别", "projects"),
        ):
            if field in data:
                values, error = normalize_ids(data[field], label)
                if error or values is None:
                    db.rollback()
                    return jsonify(ok=False, error=error or f"{label}不存在"), 400
                relation_table, key = (("member_groups", "group_id") if field == "group_ids"
                                       else ("member_projects", "project_id"))
                existing = {int(row[0]) for row in db.execute(
                    f"SELECT {key} FROM {relation_table} WHERE member_id=?", (member_id,)
                )}
                if not active_ids(db, table, [id_ for id_ in values if id_ not in existing]):
                    db.rollback()
                    return jsonify(ok=False, error=f"{label}已删除或不存在"), 400
                relations[field] = values

        # A changed login name, role, account status or password revokes other
        # sessions. When editing one's own name, only this session is retained.
        revoke = any(field in updates for field in ("username", "role", "is_active", "password_hash"))
        if updates:
            columns = ", ".join(f"{key}=?" for key in updates)
            statement = f"UPDATE members SET {columns}"
            if revoke:
                statement += ", session_version=session_version+1"
            db.execute(statement + " WHERE id=?", (*updates.values(), member_id))
        if "group_ids" in relations:
            _set_member_groups(db, member_id, relations["group_ids"])
        if "project_ids" in relations:
            _set_member_projects(db, member_id, relations["project_ids"])
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify(ok=False, error="用户名已被其他成员使用"), 409
    except Exception:
        db.rollback()
        raise

    if revoke and member_id == actor["id"]:
        session["session_version"] = target["session_version"] + 1
    return jsonify(ok=True, profile=_profile(db, member_id))


@bp.route("/api/account/profile", methods=["GET", "POST"])
@login_required
def account_profile():
    actor = current_user()
    if request.method == "GET":
        return jsonify(ok=True, profile=_profile(get_db(), actor["id"]))
    return _save(actor["id"], as_admin=False)


@bp.route("/api/account/preferences", methods=["GET", "POST"])
@login_required
def account_preferences():
    actor = current_user()
    if request.method == "GET":
        return jsonify(ok=True, **preferences(actor))
    data = request.get_json(silent=True)
    saved = preferences(actor)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="外观设置格式不正确"), 400
    if set(data) == {"theme"}:
        # Existing two-mode clients may still switch without losing a palette.
        if not isinstance(data["theme"], str) or data["theme"] not in {"light", "dark"}:
            return jsonify(ok=False, error="请选择亮色或暗色主题"), 400
        saved.update(mode=data["theme"], theme=data["theme"])
    elif set(data) == {"mode", "light_theme", "dark_theme"}:
        if (not isinstance(data["mode"], str) or data["mode"] not in THEME_MODES
                or not isinstance(data["light_theme"], str) or data["light_theme"] not in LIGHT_THEMES
                or not isinstance(data["dark_theme"], str) or data["dark_theme"] not in DARK_THEMES):
            return jsonify(ok=False, error="请选择有效的主题模式及亮色、暗色配色"), 400
        saved.update(data)
        if saved["mode"] != "system":
            saved["theme"] = saved["mode"]
    else:
        return jsonify(ok=False, error="外观设置包含缺失或不允许修改的字段"), 400
    db = get_db()
    # Preferences never change session_version or another member's settings.
    with db:
        db.execute("UPDATE members SET ui_theme=?,theme_mode=?,light_theme=?,dark_theme=? WHERE id=?",
                   (saved["theme"], saved["mode"], saved["light_theme"], saved["dark_theme"], actor["id"]))
    return jsonify(ok=True, **saved)


@bp.route("/api/member/<int:member_id>/profile", methods=["POST"])
@login_required
def member_profile(member_id: int):
    return _save(member_id, as_admin=True)


@bp.route("/api/member/<int:member_id>/delete", methods=["POST"])
@login_required
def delete_member(member_id: int):
    """Admin-only reversible removal; never break foreign keys or historical logs."""
    actor = current_user()
    if actor["role"] != "admin":
        return jsonify(ok=False, error="仅管理员可删除成员"), 403
    if member_id <= 0 or member_id > MAX_SQLITE_INTEGER:
        return jsonify(ok=False, error="ID 格式不正确"), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"confirm_username"}:
        return jsonify(ok=False, error="必须确认被删除成员的用户名"), 400
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT username,deleted_at FROM members WHERE id=?", (member_id,)).fetchone()
    if row is None:
        db.rollback()
        return jsonify(ok=False, error="成员不存在"), 404
    if member_id == actor["id"]:
        db.rollback()
        return jsonify(ok=False, error="不能删除自己"), 403
    if row["deleted_at"] is not None:
        db.rollback()
        return jsonify(ok=False, error="成员已删除"), 409
    if data["confirm_username"] != row["username"]:
        db.rollback()
        return jsonify(ok=False, error="确认用户名不匹配"), 400
    from common import now_str
    db.execute(
        "UPDATE members SET deleted_at=?,is_active=0,session_version=session_version+1 WHERE id=?",
        (now_str(), member_id),
    )
    db.commit()
    return jsonify(ok=True)


@bp.route("/api/member/<int:member_id>/restore", methods=["POST"])
@login_required
def restore_member(member_id: int):
    actor = current_user()
    if actor["role"] != "admin":
        return jsonify(ok=False, error="仅管理员可恢复成员"), 403
    if member_id <= 0 or member_id > MAX_SQLITE_INTEGER:
        return jsonify(ok=False, error="ID 格式不正确"), 400
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT deleted_at FROM members WHERE id=?", (member_id,)).fetchone()
    if row is None:
        db.rollback()
        return jsonify(ok=False, error="成员不存在"), 404
    if row["deleted_at"] is None:
        db.rollback()
        return jsonify(ok=False, error="成员未被删除"), 409
    # Restored accounts remain disabled until explicitly enabled by the admin.
    db.execute("UPDATE members SET deleted_at=NULL,session_version=session_version+1 WHERE id=?", (member_id,))
    db.commit()
    return jsonify(ok=True)
