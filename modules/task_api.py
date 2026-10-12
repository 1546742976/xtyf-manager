"""HTTP routes for collaboration tasks."""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from common import current_user, get_db, login_required, manager_required
from modules.task_delete import delete_task
from modules.task_mutations import PRIORITIES, STATUSES, create_task, update_task
from modules.task_queries import task_data
from modules.task_revisions import list_revisions, revert_revision
from modules.task_types import JsonObject
from modules.task_validation import MAX_SQLITE_INTEGER, InvalidIdError, parse_id

bp = Blueprint("collaboration_tasks", __name__)


def _path_id_error(*ids: int) -> tuple[Response, int] | None:
    if any(item <= 0 or item > MAX_SQLITE_INTEGER for item in ids):
        return jsonify(ok=False, error="ID 格式不正确"), 400
    return None


def _json_body() -> tuple[JsonObject | None, tuple[Response, int] | None]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify(ok=False, error="请求格式不正确"), 400)
    return data, None


@bp.route("/api/data")
@login_required
def data():
    members, all_members, groups, projects, tasks = task_data(get_db())
    public_members = [{key: value for key, value in member.items() if key != "username"}
                      for member in members]
    public_all = [{key: value for key, value in member.items() if key != "username"}
                  for member in all_members]
    return jsonify(ok=True, me=current_user(), members=public_members, all_members=public_all,
                   groups=groups, projects=projects, tasks=tasks,
                   statuses=STATUSES, priorities=PRIORITIES)


@bp.route("/api/task", methods=["POST"])
@manager_required
def create():
    body, response = _json_body()
    if response is not None:
        return response
    try:
        task_id, version, error, status = create_task(get_db(), current_user(), body)
    except (TypeError, ValueError):
        get_db().rollback()
        return jsonify(ok=False, error="ID 或版本格式不正确"), 400
    if error:
        return jsonify(ok=False, error=error), status
    return jsonify(ok=True, id=task_id, version=version)


@bp.route("/api/task/<int:task_id>", methods=["POST"])
@login_required
def update(task_id: int):
    if (response := _path_id_error(task_id)) is not None:
        return response
    body, response = _json_body()
    if response is not None:
        return response
    try:
        version, error, status = update_task(get_db(), current_user(), task_id, body)
    except (TypeError, ValueError):
        get_db().rollback()
        return jsonify(ok=False, error="ID 或版本格式不正确"), 400
    if error:
        return jsonify(ok=False, error=error), status
    return jsonify(ok=True, id=task_id, version=version)


@bp.route("/api/task/<int:task_id>/delete", methods=["POST"])
@manager_required
def delete(task_id: int):
    if (response := _path_id_error(task_id)) is not None:
        return response
    body = request.get_json(silent=True)
    if body is not None and not isinstance(body, dict):
        return jsonify(ok=False, error="请求格式不正确"), 400
    try:
        expected = parse_id(body.get("version"), "version", False) if body and "version" in body else None
    except InvalidIdError:
        return jsonify(ok=False, error="version 必须是有效整数"), 400
    version, error, status = delete_task(get_db(), current_user(), task_id, expected)
    if error:
        return jsonify(ok=False, error=error), status
    return jsonify(ok=True, id=task_id, version=version)


@bp.route("/api/task/<int:task_id>/logs")
@login_required
def logs(task_id: int):
    if (response := _path_id_error(task_id)) is not None:
        return response
    db = get_db()
    if db.execute("SELECT 1 FROM tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone() is None:
        return jsonify(ok=False, error="任务不存在"), 404
    rows = db.execute(
        "SELECT id,member_name,note,changes,created_at FROM logs WHERE task_id=? ORDER BY id DESC LIMIT 100",
        (task_id,),
    ).fetchall()
    return jsonify(ok=True, logs=[dict(row) for row in rows])


@bp.route("/api/task/<int:task_id>/revisions")
@login_required
def revisions(task_id: int):
    if (response := _path_id_error(task_id)) is not None:
        return response
    items = list_revisions(get_db(), task_id)
    if items is None:
        return jsonify(ok=False, error="任务不存在"), 404
    return jsonify(ok=True, revisions=items)


@bp.route("/api/task/<int:task_id>/revisions/<int:revision_id>/revert", methods=["POST"])
@login_required
def revert(task_id: int, revision_id: int):
    if (response := _path_id_error(task_id, revision_id)) is not None:
        return response
    user = current_user()
    if user["role"] != "admin":
        return jsonify(ok=False, error="仅管理员可撤销修订"), 403
    body, response = _json_body()
    if response is not None:
        return response
    try:
        value = body["version"]
        if isinstance(value, bool) or isinstance(value, float):
            raise TypeError
        if not isinstance(value, (int, str)) or (isinstance(value, str) and not value.isdigit()):
            raise TypeError
        expected = int(value)
        if expected <= 0 or expected > MAX_SQLITE_INTEGER:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return jsonify(ok=False, error="version 必填且必须为整数"), 400
    version, error, status = revert_revision(get_db(), user, task_id, revision_id, expected)
    if error:
        return jsonify(ok=False, error=error), status
    return jsonify(ok=True, id=task_id, version=version)
