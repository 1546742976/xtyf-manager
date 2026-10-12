"""Structured task revision listing and safe per-change reverts."""
from __future__ import annotations

import json
import sqlite3
from common import now_str
from modules.task_mutations import RELATION_FIELDS, SCALAR_FIELDS, _relations, _replace_relations, _revision, _validate
from modules.task_types import Actor, JsonObject


def list_revisions(db: sqlite3.Connection, task_id: int) -> list[JsonObject] | None:
    task = db.execute("SELECT id FROM tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone()
    if task is None:
        return None
    rows = db.execute(
        "SELECT id,actor_name,created_at,before_json,after_json,version,reverted_by,kind "
        "FROM task_revisions WHERE task_id=? ORDER BY id DESC", (task_id,),
    ).fetchall()
    return [{"id": row["id"], "actor_name": row["actor_name"], "created_at": row["created_at"],
             "before": json.loads(row["before_json"]), "after": json.loads(row["after_json"]),
             "version": row["version"], "reverted_by": row["reverted_by"], "kind": row["kind"]}
            for row in rows]


def revert_revision(
    db: sqlite3.Connection, actor: Actor, task_id: int, revision_id: int, expected: int
) -> tuple[int | None, str | None, int]:
    db.execute("BEGIN IMMEDIATE")
    task_row = db.execute("SELECT * FROM tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone()
    if task_row is None:
        db.rollback(); return None, "任务不存在", 404
    target = db.execute("SELECT * FROM task_revisions WHERE id=? AND task_id=?", (revision_id, task_id)).fetchone()
    if target is None:
        db.rollback(); return None, "修订记录不存在", 404
    if target["reverted_by"] is not None:
        db.rollback(); return None, "该修订已撤销", 409
    if target["kind"] not in ("update", "revert"):
        db.rollback(); return None, "该修订不能撤销", 400
    before_target = json.loads(target["before_json"])
    touched = set(before_target)
    for row in db.execute(
        "SELECT before_json,after_json FROM task_revisions WHERE task_id=? AND id>? ORDER BY id",
        (task_id, revision_id),
    ).fetchall():
        if touched.intersection(json.loads(row["before_json"])) or touched.intersection(json.loads(row["after_json"])):
            db.rollback(); return None, "目标字段已有后续修改", 409
    values = {key: task_row[key] for key in SCALAR_FIELDS}
    relations = _relations(db, task_id)
    current: JsonObject = {}
    for key in touched:
        current[key] = values[key] if key in SCALAR_FIELDS else relations[key]
        if key in SCALAR_FIELDS:
            values[key] = before_target[key]
        elif key in RELATION_FIELDS:
            relations[key] = before_target[key]
    active_members = relations["participant_ids"] if "participant_ids" in touched else []
    if "owner_id" in touched and values["owner_id"] is not None:
        active_members = active_members + [int(values["owner_id"])]
    active_groups = relations["group_ids"] if "group_ids" in touched else []
    active_project = int(values["project_id"]) if "project_id" in touched and values["project_id"] is not None else None
    error = _validate(db, task_id, values, relations, active_members, active_groups, active_project)
    if error:
        db.rollback(); return None, error, 400
    try:
        row = db.execute("SELECT version FROM tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone()
        if row is None:
            db.rollback(); return None, "任务不存在", 404
        if int(row[0]) != expected:
            db.rollback(); return None, "任务已被其他人修改", 409
        version = expected + 1
        assignments = ",".join(f"{key}=?" for key in SCALAR_FIELDS)
        db.execute(f"UPDATE tasks SET {assignments},updated_at=?,version=? WHERE id=? AND version=?",
                   (*values.values(), now_str(), version, task_id, expected))
        _replace_relations(db, task_id, relations)
        new_id = _revision(db, task_id, actor, current, before_target, version, "revert", revision_id)
        db.execute("UPDATE task_revisions SET reverted_by=? WHERE id=?", (new_id, revision_id))
        db.execute("INSERT INTO logs(task_id,member_id,member_name,note,changes,created_at) VALUES(?,?,?,?,?,?)",
                   (task_id, actor["id"], actor["name"], "", f"撤销修订 #{revision_id}", now_str()))
        db.commit()
        return version, None, 200
    except sqlite3.Error:
        db.rollback()
        raise
