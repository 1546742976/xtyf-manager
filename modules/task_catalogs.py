"""Group, project, and member-group collaboration APIs."""
from __future__ import annotations

import secrets
from modules.input_validation import clean_text, valid_password
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
        return jsonify(ok=False, error="仅超级管理员可删除或恢复组别"), 403
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
    try:
        name = clean_text(data.get('name'), '姓名', 64, allow_newlines=False)
        username = clean_text(data.get('username') or name, '用户名', 64, allow_newlines=False)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    generated = data.get("password") in (None, "")
    password = secrets.token_urlsafe(10) if generated else data["password"]
    if not valid_password(password):
        return jsonify(ok=False, error="初始密码必须为 8 至 1024 位文字"), 400
    role = data.get("role", "member")
    if not isinstance(role, str) or role not in ("admin", "manager", "member"):
        return jsonify(ok=False, error="角色必须为有效的文字选项"), 400
    user = current_user()
    if user["role"] != "admin":
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
    text = data.get('text', '')
    if not isinstance(text, str):
        return jsonify(ok=False, error='导入内容必须是文字'), 400
    # splitlines also treats control characters such as \x1e as row separators.
    # Preserve them for validation; accept only the usual LF/CRLF line endings.
    lines = text.replace('\r\n', '\n').split('\n')
    if text.endswith('\n'):
        lines.pop()
    if len(lines) > 500:
        return jsonify(ok=False, error='每次最多导入 500 行'), 400
    created, failures, prepared = [], [], []
    # Reserve before validation: no inserts until every row passes.
    db.execute('BEGIN IMMEDIATE')
    try:
        existing = {row[0] for row in db.execute('SELECT username FROM members')}
        groups = {row['name']: row['id'] for row in db.execute('SELECT id,name FROM groups WHERE deleted_at IS NULL')}
        if not active_ids(db, 'projects', project_ids) or ('group_ids' in data and not active_ids(db, 'groups', requested_groups or [])):
            raise ValueError('组别已变化，请重新核对')
        for line, raw in enumerate(lines, 1):
            if raw.strip().startswith('#'):
                continue
            try:
                if any(ord(char) < 32 and char != '\t' or ord(char) == 127 for char in raw):
                    raise ValueError('行内容不能包含控制字符')
                parts = [part.strip() for part in raw.replace('，', ',').replace('\t', ',').split(',')]
                if len(parts) > 3:
                    raise ValueError('每行应为姓名、组别、用户名，最多 3 列')
                name = clean_text(parts[0], '姓名', 64, allow_newlines=False)
                username = clean_text(parts[2] if len(parts) > 2 and parts[2] else name, '用户名', 64, allow_newlines=False)
                group_name = clean_text(parts[1] if len(parts) > 1 else '', '组别', 200, required=False, allow_newlines=False)
                if username in existing:
                    raise ValueError(f'用户名 {username} 已存在或在本批重复')
                existing.add(username)
                names = [v.strip() for v in group_name.split(';') if v.strip()]
                if any(v not in groups for v in names):
                    raise ValueError('专业组不存在或已停用，请先创建专业组')
                ids = sorted({groups[v] for v in names})
                if 'group_ids' in data and ids and ids != sorted(requested_groups or []):
                    raise ValueError('导入组别与 group_ids 冲突')
                prepared.append((line, name, username, requested_groups if 'group_ids' in data else ids))
            except ValueError as exc:
                failures.append({'line': line, 'reason': str(exc)})
        if failures:
            db.rollback()
            return jsonify(ok=False, error='整批校验失败，未创建任何成员', created=[], failures=failures), 400
        for line, name, username, ids in prepared:
            password = secrets.token_urlsafe(9)
            member_id = int(db.execute(
                'INSERT INTO members(name,username,password_hash,group_name,role,created_at,must_change_password) VALUES(?,?,?,?,?,?,1)',
                (name, username, generate_password_hash(password), '', 'member', now_str()),
            ).lastrowid)
            _set_member_groups(db, member_id, ids)
            _set_member_projects(db, member_id, project_ids)
            created.append(f'{name}（{username} / {password}）')
        db.commit()
    except (ValueError, sqlite3.IntegrityError) as exc:
        db.rollback()
        return jsonify(ok=False, error='整批导入已回滚，请重新核对', created=[],
                       failures=[{'line': locals().get('line', 0), 'reason': str(exc) if isinstance(exc, ValueError) else '用户名或组别发生并发冲突'}]), 409
    except Exception:
        db.rollback()
        raise
    return jsonify(ok=True, created=created, failures=[])
