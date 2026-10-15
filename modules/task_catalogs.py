"""Group, project, and member-group collaboration APIs."""
from __future__ import annotations

import secrets
import sqlite3

from flask import Blueprint, Response, jsonify, request
from werkzeug.security import generate_password_hash

from common import current_user, get_db, login_required, manager_required, now_str
from modules.task_validation import MAX_SQLITE_INTEGER, active_ids, normalize_ids

bp = Blueprint("task_catalogs", __name__)


def _path_id_error(item_id: int) -> tuple[Response, int] | None:
    if item_id <= 0 or item_id > MAX_SQLITE_INTEGER:
        return jsonify(ok=False, error="ID 格式不正确"), 400
    return None


def _catalog_response(table: str) -> Response:
    rows = [dict(row) for row in get_db().execute(
        f"SELECT id,name,CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END AS is_active FROM {table} ORDER BY CASE WHEN deleted_at IS NULL THEN 0 ELSE 1 END, name, id"
    )]
    return jsonify(ok=True, **{table: rows})


def _catalog_write(table: str, item_id: int | None = None) -> Response | tuple[Response, int]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请求格式不正确"), 400
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, error="名称不能为空"), 400
    if len(name) > 80 or (table == "groups" and ";" in name):
        return jsonify(ok=False, error="名称不得超过 80 字；组别名称不能包含分号"), 400
    db = get_db()
    try:
        if item_id is None:
            item_id = int(db.execute(f"INSERT INTO {table}(name) VALUES (?)", (name,)).lastrowid)
        else:
            if db.execute(f"SELECT 1 FROM {table} WHERE id=?", (item_id,)).fetchone() is None:
                return jsonify(ok=False, error="目录项不存在"), 404
            db.execute(f"UPDATE {table} SET name=? WHERE id=?", (name, item_id))
            if table == "groups":
                # Keep flat legacy columns in sync while preserving stable memberships.
                for target, relation, owner_col in (
                    ("members", "member_groups", "member_id"),
                    ("tasks", "task_groups", "task_id"),
                ):
                    db.execute(
                        f"UPDATE {target} SET group_name=COALESCE(("
                        f"SELECT GROUP_CONCAT(name, ';') FROM ("
                        f"SELECT g.name AS name FROM {relation} r JOIN groups g ON g.id=r.group_id "
                        f"WHERE r.{owner_col}={target}.id ORDER BY g.id"
                        f")), '') WHERE id IN (SELECT {owner_col} FROM {relation} WHERE group_id=?)",
                        (item_id,),
                    )
            elif table == "projects":
                # Support old integrations reading the flat legacy column.
                db.execute("UPDATE tasks SET project=? WHERE project_id=?", (name, item_id))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return jsonify(ok=False, error="名称已存在"), 409
    return jsonify(ok=True, id=item_id, **{table: [dict(row) for row in db.execute(
        f"SELECT id,name,CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END AS is_active FROM {table} ORDER BY CASE WHEN deleted_at IS NULL THEN 0 ELSE 1 END, name, id"
    )]})


@bp.route("/api/groups", methods=["GET"])
@login_required
def groups_get():
    return _catalog_response("groups")


@bp.route("/api/groups", methods=["POST"])
@manager_required
def groups_create():
    return _catalog_write("groups")


@bp.route("/api/groups/<int:item_id>", methods=["POST"])
@manager_required
def groups_edit(item_id: int):
    if (response := _path_id_error(item_id)) is not None:
        return response
    return _catalog_write("groups", item_id)


def _archive_catalog(table: str, item_id: int, archive: bool):
    actor = current_user()
    if actor["role"] != "admin":
        return jsonify(ok=False, error="仅管理员可删除或恢复组别"), 403
    if (response := _path_id_error(item_id)) is not None:
        return response
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    row = db.execute(f"SELECT id,name,deleted_at FROM {table} WHERE id=?", (item_id,)).fetchone()
    if row is None:
        db.rollback()
        return jsonify(ok=False, error="组别不存在"), 404
    if archive:
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get("confirm_name") != row["name"]:
            db.rollback()
            return jsonify(ok=False, error="请确认要删除的组别名称"), 400
    if (row["deleted_at"] is not None) == archive:
        db.rollback()
        return jsonify(ok=False, error="组别已处于目标状态"), 409
    db.execute(f"UPDATE {table} SET deleted_at=? WHERE id=?",
               (now_str() if archive else None, item_id))
    db.commit()
    # Existing task references remain readable; archived groups are no longer assignable.
    return jsonify(ok=True, id=item_id, is_active=0 if archive else 1)


@bp.route("/api/groups/<int:item_id>/delete", methods=["POST"])
@login_required
def groups_delete(item_id: int):
    return _archive_catalog("groups", item_id, True)


@bp.route("/api/groups/<int:item_id>/restore", methods=["POST"])
@login_required
def groups_restore(item_id: int):
    return _archive_catalog("groups", item_id, False)


@bp.route("/api/projects/<int:item_id>/delete", methods=["POST"])
@login_required
def projects_delete(item_id: int):
    return _archive_catalog("projects", item_id, True)


@bp.route("/api/projects/<int:item_id>/restore", methods=["POST"])
@login_required
def projects_restore(item_id: int):
    return _archive_catalog("projects", item_id, False)


@bp.route("/api/projects", methods=["GET"])
@login_required
def projects_get():
    return _catalog_response("projects")


@bp.route("/api/projects", methods=["POST"])
@manager_required
def projects_create():
    return _catalog_write("projects")


@bp.route("/api/projects/<int:item_id>", methods=["POST"])
@manager_required
def projects_edit(item_id: int):
    if (response := _path_id_error(item_id)) is not None:
        return response
    return _catalog_write("projects", item_id)


def _set_member_projects(db: sqlite3.Connection, member_id: int, project_ids: list[int]) -> None:
    db.execute("DELETE FROM member_projects WHERE member_id=?", (member_id,))
    db.executemany("INSERT INTO member_projects(member_id,project_id) VALUES (?,?)",
                   ((member_id, project_id) for project_id in project_ids))


def _member_project_ids(db: sqlite3.Connection) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    for member_id, project_id in db.execute("SELECT member_id,project_id FROM member_projects ORDER BY project_id"):
        result.setdefault(int(member_id), []).append(int(project_id))
    return result


def _set_member_groups(db: sqlite3.Connection, member_id: int, group_ids: list[int]) -> None:
    db.execute("DELETE FROM member_groups WHERE member_id=?", (member_id,))
    db.executemany("INSERT INTO member_groups(member_id,group_id) VALUES(?,?)",
                   ((member_id, group_id) for group_id in group_ids))
    names = [row[0] for row in db.execute(
        f"SELECT name FROM groups WHERE id IN ({','.join('?' for _ in group_ids)}) ORDER BY name",
        group_ids,
    )] if group_ids else []
    db.execute("UPDATE members SET group_name=? WHERE id=?", (";".join(names), member_id))


def _legacy_groups(db: sqlite3.Connection, text: str, split: bool) -> list[int]:
    names = [part.strip() for part in text.split(";")] if split else [text.strip()]
    ids: list[int] = []
    for name in names:
        if not name:
            continue
        db.execute("INSERT OR IGNORE INTO groups(name) VALUES(?)", (name,))
        ids.append(int(db.execute("SELECT id FROM groups WHERE name=?", (name,)).fetchone()[0]))
    return ids


@bp.route("/api/member/<int:member_id>/groups", methods=["POST"])
@manager_required
def member_groups(member_id: int):
    if (response := _path_id_error(member_id)) is not None:
        return response
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请求格式不正确"), 400
    group_ids, error = normalize_ids(data.get("group_ids"), "组别")
    db = get_db()
    if error or group_ids is None:
        return jsonify(ok=False, error=error or "组别不存在"), 400
    existing = {int(r[0]) for r in db.execute("SELECT group_id FROM member_groups WHERE member_id=?", (member_id,))}
    if not active_ids(db, "groups", [x for x in group_ids if x not in existing]):
        return jsonify(ok=False, error="专业组已删除或不存在"), 400
    if db.execute("SELECT 1 FROM members WHERE id=?", (member_id,)).fetchone() is None:
        return jsonify(ok=False, error="成员不存在"), 404
    _set_member_groups(db, member_id, group_ids)
    db.commit()
    return jsonify(ok=True, group_ids=group_ids)


@bp.route("/api/member/<int:member_id>/projects", methods=["POST"])
@manager_required
def member_projects(member_id: int):
    if (response := _path_id_error(member_id)) is not None:
        return response
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请求格式不正确"), 400
    project_ids, error = normalize_ids(data.get("project_ids"), "造车组别")
    db = get_db()
    if error or project_ids is None:
        return jsonify(ok=False, error=error or "造车组别不存在"), 400
    existing = {int(r[0]) for r in db.execute("SELECT project_id FROM member_projects WHERE member_id=?", (member_id,))}
    if not active_ids(db, "projects", [x for x in project_ids if x not in existing]):
        return jsonify(ok=False, error="造车组别已删除或不存在"), 400
    if db.execute("SELECT 1 FROM members WHERE id=?", (member_id,)).fetchone() is None:
        return jsonify(ok=False, error="成员不存在"), 404
    _set_member_projects(db, member_id, project_ids)
    db.commit()
    return jsonify(ok=True, project_ids=project_ids)


@bp.route("/api/members", methods=["GET"])
@manager_required
def members_get():
    db = get_db()
    rows = [dict(row) for row in db.execute(
        "SELECT id,name,username,group_name,role,is_active,deleted_at,must_change_password FROM members "
        "WHERE deleted_at IS NULL OR ? ORDER BY group_name,id",
        (current_user()["role"] == "admin" and request.args.get("include_deleted") == "1",)
    )]
    group_map: dict[int, list[int]] = {int(row["id"]): [] for row in rows}
    for member_id, group_id in db.execute("SELECT member_id,group_id FROM member_groups ORDER BY group_id"):
        group_map.setdefault(int(member_id), []).append(int(group_id))
    names = {int(row["id"]): str(row["name"]) for row in db.execute("SELECT id,name FROM groups")}
    projects = _member_project_ids(db)
    return jsonify(ok=True, members=[row | {"is_deleted": row["deleted_at"] is not None,
        "project_ids": projects.get(int(row["id"]), []),
        "group_ids": group_map[int(row["id"])],
        "group_name": ";".join(names[group_id] for group_id in group_map[int(row["id"])]),
    } for row in rows])


@bp.route("/api/member", methods=["POST"])
@manager_required
def member_create():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请求格式不正确"), 400
    name = str(data.get("name") or "").strip()
    username = str(data.get("username") or "").strip() or name
    password = str(data.get("password") or "") or secrets.token_urlsafe(10)
    generated = not bool(data.get("password"))
    if not name or len(password) < 8:
        return jsonify(ok=False, error="姓名不能为空且初始密码至少 8 位"), 400
    role = str(data.get("role") or "member")
    user = current_user()
    if user["role"] != "admin" or role not in ("admin", "manager", "member"):
        role = "member"
    db = get_db()
    group_ids, error = normalize_ids(data.get("group_ids"), "组别")
    if "group_name" in data:
        legacy = _legacy_groups(db, str(data.get("group_name") or ""), False)
        if "group_ids" in data and group_ids != legacy:
            db.rollback(); return jsonify(ok=False, error="group_name 与 group_ids 冲突"), 400
        group_ids = legacy
    group_ids = group_ids or []
    project_ids, project_error = normalize_ids(data.get("project_ids"), "造车组别")
    if project_error or project_ids is None or not active_ids(db, "projects", project_ids):
        db.rollback(); return jsonify(ok=False, error=project_error or "造车组别不存在"), 400
    if error or not active_ids(db, "groups", group_ids):
        db.rollback(); return jsonify(ok=False, error=error or "组别不存在"), 400
    try:
        member_id = int(db.execute(
            "INSERT INTO members(name,username,password_hash,group_name,role,created_at,must_change_password) VALUES(?,?,?,?,?,?,1)",
            (name, username, generate_password_hash(password), "", role, now_str()),
        ).lastrowid)
        _set_member_groups(db, member_id, group_ids)
        _set_member_projects(db, member_id, project_ids)
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback(); return jsonify(ok=False, error=f"用户名 {username} 已存在"), 400
    return jsonify(ok=True, id=member_id, initial_password=password if generated else None)


@bp.route("/api/members/import", methods=["POST"])
@manager_required
def members_import():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error="请求格式不正确"), 400
    db = get_db()
    requested_groups, group_error = normalize_ids(data.get("group_ids"), "组别")
    if group_error or ("group_ids" in data and not active_ids(db, "groups", requested_groups or [])):
        return jsonify(ok=False, error=group_error or "组别不存在"), 400
    project_ids, project_error = normalize_ids(data.get("project_ids"), "造车组别")
    if project_error or project_ids is None or not active_ids(db, "projects", project_ids):
        return jsonify(ok=False, error=project_error or "造车组别不存在"), 400
    existing = {row[0] for row in db.execute("SELECT username FROM members")}
    created: list[str] = []
    for raw in str(data.get("text") or "").splitlines():
        parts = [part.strip() for part in raw.replace("，", ",").replace("\t", ",").split(",")]
        if not parts or not parts[0] or parts[0].startswith("#"):
            continue
        name = parts[0]
        username = parts[2] if len(parts) > 2 and parts[2] else name
        suffix = 2
        while username in existing:
            username = f"{name}{suffix}"; suffix += 1
        password = secrets.token_urlsafe(9)
        member_id = int(db.execute(
            "INSERT INTO members(name,username,password_hash,group_name,role,created_at,must_change_password) VALUES(?,?,?,?,?,?,1)",
            (name, username, generate_password_hash(password), "", "member", now_str()),
        ).lastrowid)
        legacy_groups = _legacy_groups(db, parts[1] if len(parts) > 1 else "", True)
        if "group_ids" in data and legacy_groups and legacy_groups != requested_groups:
            db.rollback(); return jsonify(ok=False, error="导入组别与 group_ids 冲突"), 400
        _set_member_groups(db, member_id, requested_groups if "group_ids" in data else legacy_groups)
        _set_member_projects(db, member_id, project_ids)
        existing.add(username); created.append(f"{name}（{username} / {password}）")
    db.commit()
    return jsonify(ok=True, created=created)
