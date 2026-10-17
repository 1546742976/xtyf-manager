"""Transactional logical task deletion."""
from __future__ import annotations

import sqlite3

from common import now_str
from modules.task_mutations import _revision
from modules.task_types import Actor


def delete_task(
    db: sqlite3.Connection, actor: Actor, task_id: int, expected_version: int | None
) -> tuple[int | None, str | None, int]:
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT version FROM tasks WHERE id=? AND deleted_at IS NULL", (task_id,)).fetchone()
    if row is None:
        db.rollback()
        return None, "任务不存在", 404
    snapshot_version = int(row[0])
    expected = snapshot_version if expected_version is None else expected_version
    if snapshot_version != expected:
        db.rollback()
        return None, "任务已被其他人修改", 409
    child = db.execute(
        "SELECT 1 FROM tasks WHERE parent_id=? AND deleted_at IS NULL", (task_id,)
    ).fetchone()
    dependent = db.execute(
        "SELECT 1 FROM task_dependencies d JOIN tasks t ON t.id=d.task_id "
        "WHERE d.predecessor_id=? AND t.deleted_at IS NULL", (task_id,)
    ).fetchone()
    if child or dependent:
        db.rollback()
        return None, "任务仍被后续任务作为主前置或其他前置依赖", 409
    version = expected + 1
    stamp = now_str()
    try:
        updated = db.execute(
            "UPDATE tasks SET deleted_at=?,updated_at=?,version=? "
            "WHERE id=? AND version=? AND deleted_at IS NULL",
            (stamp, stamp, version, task_id, expected),
        )
        if updated.rowcount != 1:
            db.rollback()
            return None, "任务已被其他人修改", 409
        _revision(db, task_id, actor, {"deleted_at": None}, {"deleted_at": stamp}, version, "delete")
        db.execute(
            "INSERT INTO logs(task_id,member_id,member_name,note,changes,created_at) VALUES(?,?,?,?,?,?)",
            (task_id, actor["id"], actor["name"], "", "逻辑删除", stamp),
        )
        db.commit()
        return version, None, 200
    except sqlite3.Error:
        db.rollback()
        raise
