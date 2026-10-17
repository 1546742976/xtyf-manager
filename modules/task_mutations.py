"""Transactional task creation, updates, and logical deletion."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from common import now_str
from modules.task_dates import apply_start_date_request
from modules.task_types import Actor, JsonObject
from modules.task_state import BLOCKER_FIELDS, COMPLETION_FIELDS, blocker_values, completion_values, record_blocker_event
from modules.task_validation import active_ids, creates_combined_task_cycle, normalize_ids, parse_id, valid_tasks, validate_dates

STATUSES = ("未开始", "进行中", "已完成", "阻塞", "已取消")
PRIORITIES = ("高", "中", "低")
SCALAR_FIELDS = ("title", "detail", "owner_id", "project_id", "parent_id", "priority", "status", "progress", "start_date", "due_date", "start_date_origin", "completed_at", "schedule_locked") + BLOCKER_FIELDS
REVISION_FIELDS = SCALAR_FIELDS + ("schedule_stage",)
RELATION_FIELDS = ("participant_ids", "group_ids", "predecessor_ids")


def completion_stamp() -> str:
    """Persist the completion day in Beijing regardless of the host timezone."""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _legacy_catalog(db: sqlite3.Connection, table: str, name: str, may_create: bool) -> int | None:
    clean = name.strip()
    if not clean:
        return None
    row = db.execute(f"SELECT id FROM {table} WHERE name = ?", (clean,)).fetchone()
    if row:
        return int(row[0])
    if not may_create:
        return None
    return int(db.execute(f"INSERT INTO {table}(name) VALUES (?)", (clean,)).lastrowid)


def _relations(db: sqlite3.Connection, task_id: int) -> dict[str, list[int]]:
    specs = {
        "participant_ids": ("task_participants", "member_id"),
        "group_ids": ("task_groups", "group_id"),
        "predecessor_ids": ("task_dependencies", "predecessor_id"),
    }
    return {key: [int(row[0]) for row in db.execute(
        f"SELECT {column} FROM {table} WHERE task_id = ? ORDER BY {column}", (task_id,)
    )] for key, (table, column) in specs.items()}


def _resolve_relations(
    db: sqlite3.Connection, data: JsonObject, role: str, owner_id: int | None
) -> tuple[dict[str, list[int]], str | None]:
    values: dict[str, list[int]] = {}
    for key, label in (("participant_ids", "参与者"), ("group_ids", "组别"), ("predecessor_ids", "前置任务")):
        parsed, error = normalize_ids(data.get(key), label)
        if error:
            return {}, error
        values[key] = parsed or []
    if "group_name" in data:
        legacy_name = str(data.get("group_name") or "").strip()
        legacy_id = _legacy_catalog(db, "groups", legacy_name, role in ("admin", "manager"))
        if legacy_name and legacy_id is None:
            return {}, "组别不存在"
        legacy = [legacy_id] if legacy_id is not None else []
        if "group_ids" in data and values["group_ids"] != legacy:
            return {}, "group_name 与 group_ids 冲突"
        values["group_ids"] = legacy
    # Vehicle group lives in project_id. Do not infer a task's specialty
    # from the owner; retain historical task_groups without using them in UI.
    return values, None


def _validate(
    db: sqlite3.Connection, task_id: int | None, values: JsonObject, relations: dict[str, list[int]],
    active_member_ids: list[int], active_group_ids: list[int], active_project_id: int | None,
) -> str | None:
    title = str(values.get("title") or "").strip()
    if not title:
        return "任务标题不能为空"
    owner_id = values.get("owner_id")
    participant_ids = relations["participant_ids"]
    if participant_ids and owner_id is None:
        return "有参与者时必须指定负责人"
    if not active_ids(db, "members", list(dict.fromkeys(active_member_ids))):
        return "负责人或参与者不存在或已停用"
    if not active_ids(db, "groups", active_group_ids):
        return "组别不存在"
    if active_project_id is not None and not active_ids(db, "projects", [active_project_id]):
        return "造车组别不存在"
    refs = relations["predecessor_ids"]
    parent_id = values.get("parent_id")
    if parent_id is not None:
        refs = refs + [int(parent_id)]
    refs = list(dict.fromkeys(refs))
    if not valid_tasks(db, refs, task_id):
        return "主前置或其他前置任务不存在、已删除或引用自身"
    if task_id is not None and creates_combined_task_cycle(db, task_id, refs):
        return "主前置与其他前置任务形成循环"
    if values.get("priority") not in PRIORITIES or values.get("status") not in STATUSES:
        return "状态或优先级不合法"
    return validate_dates(values.get("start_date"), values.get("due_date"))


def _replace_relations(db: sqlite3.Connection, task_id: int, relations: dict[str, list[int]]) -> None:
    specs = {
        "participant_ids": ("task_participants", "member_id"),
        "group_ids": ("task_groups", "group_id"),
        "predecessor_ids": ("task_dependencies", "predecessor_id"),
    }
    for key, (table, column) in specs.items():
        db.execute(f"DELETE FROM {table} WHERE task_id = ?", (task_id,))
        db.executemany(
            f"INSERT INTO {table}(task_id, {column}) VALUES (?, ?)",
            ((task_id, item) for item in relations[key]),
        )


def _revision(
    db: sqlite3.Connection, task_id: int, actor: Actor, before: JsonObject,
    after: JsonObject, version: int, kind: str = "update", reverted_by: int | None = None,
) -> int:
    return int(db.execute(
        "INSERT INTO task_revisions(task_id,actor_id,actor_name,created_at,before_json,after_json,version,reverted_by,kind) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (task_id, actor["id"], actor["name"], now_str(), json.dumps(before, ensure_ascii=False),
         json.dumps(after, ensure_ascii=False), version, reverted_by, kind),
    ).lastrowid)


def create_task(
    db: sqlite3.Connection, actor: Actor, data: JsonObject
) -> tuple[int | None, int | None, str | None, int]:
    db.execute("BEGIN IMMEDIATE")
    if "schedule_locked" in data and type(data["schedule_locked"]) is not bool:
        db.rollback(); return None, None, "自动排期固定日期必须为布尔值", 400
    owner_id = parse_id(data.get("owner_id"), "owner_id")
    project_id = parse_id(data.get("project_id"), "project_id")
    if "project" in data:
        legacy_id = _legacy_catalog(db, "projects", str(data.get("project") or ""), True)
        if "project_id" in data and project_id != legacy_id:
            db.rollback(); return None, None, "project 与 project_id 冲突", 400
        project_id = legacy_id
    values = {"title": str(data.get("title") or "").strip(), "detail": str(data.get("detail") or "").strip(),
              "owner_id": owner_id, "project_id": project_id,
              "parent_id": parse_id(data.get("parent_id"), "parent_id"),
              "priority": data.get("priority", "中"), "status": data.get("status", "未开始"),
              "start_date": str(data.get("start_date") or "").strip(), "due_date": str(data.get("due_date") or "").strip(),
              "schedule_locked": data.get("schedule_locked", False)}
    relations, error = _resolve_relations(db, data, str(actor["role"]), owner_id)
    error = error or apply_start_date_request(db, data, values)
    member_ids = relations.get("participant_ids", []) + ([owner_id] if owner_id is not None else [])
    error = error or _validate(db, None, values, relations, member_ids, relations.get("group_ids", []), project_id)
    if error:
        db.rollback(); return None, None, error, 400
    stamp = now_str()
    initial = {'status': '未开始', 'progress': 0, 'completed_at': None}
    try:
        values.update(completion_values(initial, data, completion_stamp()))
        blockers, blocker_event = blocker_values(db, initial, values, data)
        values.update(blockers)
    except ValueError as exc:
        db.rollback(); return None, None, str(exc), 400
    try:
        columns = ",".join(values)
        placeholders = ",".join("?" for _ in values)
        cur = db.execute(
            f"INSERT INTO tasks({columns},created_by,created_at,updated_at,version) VALUES ({placeholders},?,?,?,1)",
            (*values.values(), actor["id"], stamp, stamp),
        )
        task_id = int(cur.lastrowid)
        _replace_relations(db, task_id, relations)
        after = dict(values) | dict(relations)
        _revision(db, task_id, actor, {}, after, 1, "create")
        record_blocker_event(db, task_id, actor, blocker_event, stamp)
        db.commit()
        return task_id, 1, None, 200
    except (sqlite3.Error, TypeError, ValueError):
        db.rollback()
        raise


def update_task(
    db: sqlite3.Connection, actor: Actor, task_id: int, data: JsonObject
) -> tuple[int | None, str | None, int]:
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT * FROM tasks WHERE id = ? AND deleted_at IS NULL", (task_id,)).fetchone()
    if row is None:
        db.rollback(); return None, "任务不存在", 404
    prior = dict(row)
    participant_ids = _relations(db, task_id)["participant_ids"]
    management = actor["role"] in ("admin", "manager")
    if not management and actor["id"] != prior["owner_id"] and actor["id"] not in participant_ids:
        db.rollback(); return None, "无权限", 403
    if "schedule_locked" in data and type(data["schedule_locked"]) is not bool:
        db.rollback(); return None, "自动排期固定日期必须为布尔值", 400
    state_requested = 'progress' in data or data.get('status', prior['status']) != prior['status']
    if (state_requested or any(key in data for key in BLOCKER_FIELDS)) and 'version' not in data:
        db.rollback(); return None, "状态或进度修改必须提供版本号", 400
    expected = parse_id(data.get("version", prior["version"]), "version", False)
    values = {key: prior[key] for key in SCALAR_FIELDS}
    touched: set[str] = set()
    for key in SCALAR_FIELDS:
        if key in data and key not in ("start_date_origin", "completed_at"):
            values[key] = data[key]
            touched.add(key)
    for key in ("owner_id", "project_id", "parent_id"):
        values[key] = parse_id(values[key], key)
    if "project" in data:
        legacy_name = str(data.get("project") or "").strip()
        legacy_id = _legacy_catalog(db, "projects", legacy_name, management)
        if legacy_name and legacy_id is None:
            db.rollback(); return None, "造车组别不存在", 400
        if "project_id" in data and values["project_id"] != legacy_id:
            db.rollback(); return None, "project 与 project_id 冲突", 400
        values["project_id"] = legacy_id
        touched.add("project_id")
    for key in ("title", "detail", "start_date", "due_date"):
        values[key] = str(values[key] or "").strip()
    date_error = apply_start_date_request(db, data, values, prior)
    if date_error:
        db.rollback(); return None, date_error, 400
    for key in ("start_date", "start_date_origin"):
        if values[key] != prior[key]:
            touched.add(key)
    relations = _relations(db, task_id)
    supplied_relations, error = _resolve_relations(db, data, str(actor["role"]), values["owner_id"])
    if error:
        db.rollback()
        return None, error, 400
    for key in RELATION_FIELDS:
        if key in data or (key == "group_ids" and "group_name" in data):
            relations[key] = supplied_relations[key]
            touched.add(key)
    old_relations = _relations(db, task_id)
    if not management:
        # 归属类字段只允许管理员/项管调整；负责人和参与者仍可更新状态与内容。
        ownership = []
        if values["owner_id"] != prior["owner_id"]:
            ownership.append("负责人")
        if values["project_id"] != prior["project_id"]:
            ownership.append("造车组别")
        for key, label in (("participant_ids", "参与成员"), ("group_ids", "专业组")):
            if sorted(relations[key]) != sorted(old_relations[key]):
                ownership.append(label)
        if ownership:
            db.rollback()
            return None, "只有管理员或项管可以调整" + "、".join(ownership), 403
    active_members = [item for item in relations["participant_ids"] if item not in old_relations["participant_ids"]]
    if values["owner_id"] != prior["owner_id"] and values["owner_id"] is not None:
        active_members.append(values["owner_id"])
    active_groups = [item for item in relations["group_ids"] if item not in old_relations["group_ids"]]
    active_project = values["project_id"] if values["project_id"] != prior["project_id"] else None
    error = _validate(db, task_id, values, relations, active_members, active_groups, active_project)
    if error:
        db.rollback(); return None, error, 400
    try:
        # Merely resaving a legacy record does not silently repair its history.
        if state_requested:
            values.update(completion_values(prior, data, completion_stamp()))
            touched.update(COMPLETION_FIELDS)
        blockers, blocker_event = blocker_values(db, prior, values, data)
        values.update(blockers)
        if blocker_event:
            touched.update(BLOCKER_FIELDS)
    except ValueError as exc:
        db.rollback(); return None, str(exc), 400
    before = {key: (prior[key] if key in SCALAR_FIELDS else _relations(db, task_id)[key]) for key in touched}
    after = {key: (values[key] if key in SCALAR_FIELDS else relations[key]) for key in touched}
    changed = {key for key in touched if before[key] != after[key]}
    note = str(data.get("note") or "").strip()
    try:
        current = db.execute("SELECT version FROM tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone()
        if current is None:
            db.rollback(); return None, "任务不存在", 404
        if int(current[0]) != expected:
            db.rollback(); return None, "任务已被其他人修改", 409
        version = expected
        if changed:
            version += 1
            assignments = ",".join(f"{key}=?" for key in SCALAR_FIELDS)
            db.execute(f"UPDATE tasks SET {assignments},updated_at=?,version=? WHERE id=? AND version=?",
                       (*values.values(), now_str(), version, task_id, expected))
            _replace_relations(db, task_id, relations)
            revision_keys = set(changed)
            if revision_keys.intersection(COMPLETION_FIELDS):
                revision_keys.update(COMPLETION_FIELDS)
            if blocker_event:
                revision_keys.update(BLOCKER_FIELDS)
            _revision(db, task_id, actor, {k: prior[k] if k in SCALAR_FIELDS else before[k] for k in revision_keys},
                      {k: values[k] if k in SCALAR_FIELDS else after[k] for k in revision_keys}, version)
            if values['progress'] != prior['progress']:
                db.execute('INSERT INTO task_progress_updates(task_id,actor_id,actor_name,progress,note,created_at) VALUES(?,?,?,?,?,?)',
                           (task_id, actor['id'], actor['name'], values['progress'], note or '任务状态与进度同步', now_str()))
            record_blocker_event(db, task_id, actor, blocker_event, now_str())
        if changed or note:
            db.execute("INSERT INTO logs(task_id,member_id,member_name,note,changes,created_at) VALUES(?,?,?,?,?,?)",
                       (task_id, actor["id"], actor["name"], note, "；".join(sorted(changed)), now_str()))
        db.commit()
        return version, None, 200
    except (sqlite3.Error, TypeError, ValueError):
        db.rollback()
        raise
