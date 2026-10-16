"""Preview and atomically apply a vehicle group's specialty-stage schedule."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, timedelta

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer

from common import now_str
from modules.task_mutations import _revision
from modules.task_validation import parse_id

STAGES = ("机械", "电控", "视觉")
SCHEDULE_STATUSES = ("未开始", "进行中", "阻塞")


class ScheduleError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def resolve_stage(group_names: list[str], saved: str | None) -> tuple[list[str], str | None]:
    options = [stage for stage in STAGES if stage in group_names]
    stage = options[0] if len(options) == 1 else saved if saved in options else None
    return options, stage


def _load(db: sqlite3.Connection, project_id: int) -> dict:
    project = db.execute("SELECT id,name,deleted_at FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None or project["deleted_at"] is not None:
        raise ScheduleError("造车组别不存在或已删除", 404)
    tasks = [dict(row) for row in db.execute(
        "SELECT id,title,owner_id,status,start_date,due_date,start_date_origin,schedule_stage,version "
        "FROM tasks WHERE project_id=? AND deleted_at IS NULL ORDER BY id", (project_id,)
    )]
    # Include owner memberships and catalog names in the snapshot: member edits
    # do not increment task versions, but can change the schedule classification.
    owners = [dict(row) for row in db.execute(
        "SELECT DISTINCT m.id,m.name,m.is_active,m.deleted_at FROM members m "
        "JOIN tasks t ON t.owner_id=m.id WHERE t.project_id=? AND t.deleted_at IS NULL ORDER BY m.id",
        (project_id,),
    )]
    memberships = [dict(row) for row in db.execute(
        "SELECT mg.member_id,g.id,g.name,g.deleted_at FROM member_groups mg JOIN groups g ON g.id=mg.group_id "
        "WHERE mg.member_id IN (SELECT owner_id FROM tasks WHERE project_id=? AND deleted_at IS NULL) "
        "ORDER BY mg.member_id,g.id", (project_id,),
    )]
    return {"project": dict(project), "tasks": tasks, "owners": owners, "memberships": memberships}


def _digest(source: dict) -> str:
    return hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(current_app.secret_key, salt="vehicle-task-schedule-v1")


def _plan(source: dict, raw_choices: object) -> tuple[dict, dict[str, str]]:
    if not isinstance(raw_choices, dict):
        raise ScheduleError("阶段选择必须是任务 ID 到专业组的对象")
    owners = {row["id"]: row for row in source["owners"]}
    names: dict[int, list[str]] = {}
    for row in source["memberships"]:
        if row["deleted_at"] is None:
            names.setdefault(row["member_id"], []).append(row["name"])
    tasks = {row["id"]: row for row in source["tasks"]}
    choices: dict[str, str] = {}
    for key, value in raw_choices.items():
        try:
            task_id = parse_id(key, "任务 ID", False)
        except ValueError as exc:
            raise ScheduleError("阶段选择包含非法任务 ID") from exc
        task = tasks.get(task_id)
        if task is None or task["status"] not in SCHEDULE_STATUSES:
            raise ScheduleError("阶段选择包含不参与排期的任务")
        options, _ = resolve_stage(names.get(task["owner_id"], []), task["schedule_stage"])
        if len(options) < 2 or value not in options or str(task_id) in choices:
            raise ScheduleError("请选择负责人所属的专业组阶段")
        choices[str(task_id)] = value

    rows, errors = [], []
    durations: dict[int, timedelta] = {}
    for task in source["tasks"]:
        options, stage = resolve_stage(names.get(task["owner_id"], []), choices.get(str(task["id"]), task["schedule_stage"]))
        eligible = task["status"] in SCHEDULE_STATUSES
        row = {**task, "owner_name": owners.get(task["owner_id"], {}).get("name", "未指定"),
               "stage_options": options, "stage": stage, "eligible": eligible,
               "before": {key: task[key] for key in ("start_date", "due_date", "schedule_stage")},
               "after": {key: task[key] for key in ("start_date", "due_date", "schedule_stage")}, "note": ""}
        rows.append(row)
        if not eligible:
            row["note"] = "已完成或已取消，保留原日期"
            continue
        if not options:
            row["note"] = "未归类，保留原日期"
            continue
        if stage is None:
            row["note"] = "请先选择任务阶段"
            errors.append(f"#{task['id']} {task['title']}：负责人属于多个专业组，请选择阶段")
        try:
            start, end = date.fromisoformat(task["start_date"]), date.fromisoformat(task["due_date"])
            if start.isoformat() != task["start_date"] or end.isoformat() != task["due_date"] or start > end:
                raise ValueError
            durations[task["id"]] = end - start
        except (ValueError, TypeError):
            row["note"] = "请补齐有效的开始日期和截止日期"
            errors.append(f"#{task['id']} {task['title']}：日期缺失、格式不正确或开始日期晚于截止日期")

    scheduled = [row for row in rows if row["eligible"] and row["stage"]]
    if not errors:
        previous_end = None
        for stage in STAGES:
            phase = [row for row in scheduled if row["stage"] == stage]
            if not phase:
                continue
            for row in phase:
                row["after"]["schedule_stage"] = stage if len(row["stage_options"]) > 1 else None
                if previous_end is None:
                    row["note"] = "首阶段，保持原日期"
                else:
                    try:
                        end = previous_end + durations[row["id"]]
                    except OverflowError:
                        errors.append(f"#{row['id']} {row['title']}：调整后的日期超出有效范围")
                        continue
                    row["after"]["start_date"] = previous_end.isoformat()
                    row["after"]["due_date"] = end.isoformat()
                    row["note"] = "接续上一阶段，保持原工期"
            previous_end = max(date.fromisoformat(row["after"]["due_date"]) for row in phase)
    rows.sort(key=lambda row: (STAGES.index(row["stage"]) if row["stage"] else len(STAGES),
                               row["before"]["start_date"] or "9999-99-99",
                               row["before"]["due_date"] or "9999-99-99", row["id"]))
    changes = [row for row in scheduled if row["before"] != row["after"]] if not errors else []
    return {"project": {"id": source["project"]["id"], "name": source["project"]["name"]},
            "rows": rows, "errors": errors, "can_apply": bool(scheduled) and not errors,
            "changed_count": len(changes), "changes": changes}, choices


def preview_schedule(db: sqlite3.Connection, project_id: int, choices: object) -> dict:
    db.execute("BEGIN")
    try:
        source = _load(db, project_id)
        result, normalized = _plan(source, choices)
        result["snapshot"] = _signer().dumps({"project_id": project_id, "digest": _digest(source), "choices": normalized}) if result["can_apply"] else None
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def apply_schedule(db: sqlite3.Connection, actor: dict, project_id: int, snapshot: object) -> dict:
    if not isinstance(snapshot, str) or not snapshot:
        raise ScheduleError("请先生成排期预览")
    try:
        expected = _signer().loads(snapshot)
    except BadSignature as exc:
        raise ScheduleError("排期预览无效，请重新预览") from exc
    if not isinstance(expected, dict) or expected.get("project_id") != project_id:
        raise ScheduleError("排期预览与造车组别不匹配")
    db.execute("BEGIN IMMEDIATE")
    try:
        source = _load(db, project_id)
        if expected.get("digest") != _digest(source):
            raise ScheduleError("任务或负责人专业组已变化，请重新预览；本次未保存任何调整", 409)
        result, _ = _plan(source, expected.get("choices", {}))
        if not result["can_apply"]:
            raise ScheduleError("排期条件不完整，请重新预览")
        stamp = now_str()
        for row in result["changes"]:
            changed = [key for key in row["before"] if row["before"][key] != row["after"][key]]
            # Explicitly accepted scheduling can replace manual/frozen dates.
            # Record the new origin in the same revision so undo restores both.
            origin = row["start_date_origin"]
            if any(key in changed for key in ("start_date", "due_date")):
                row["before"]["start_date_origin"] = origin
                row["after"]["start_date_origin"] = "schedule"
                origin = "schedule"
                if row["before"]["start_date_origin"] != origin:
                    changed.append("start_date_origin")
            version = row["version"] + 1
            cur = db.execute(
                "UPDATE tasks SET start_date=?,due_date=?,schedule_stage=?,start_date_origin=?,updated_at=?,version=? WHERE id=? AND version=?",
                (row["after"]["start_date"], row["after"]["due_date"], row["after"]["schedule_stage"], origin, stamp, version, row["id"], row["version"]),
            )
            if cur.rowcount != 1:
                raise ScheduleError("任务已被其他人修改，请重新预览", 409)
            _revision(db, row["id"], actor, {key: row["before"][key] for key in changed},
                      {key: row["after"][key] for key in changed}, version)
            db.execute("INSERT INTO logs(task_id,member_id,member_name,note,changes,created_at) VALUES(?,?,?,?,?,?)",
                       (row["id"], actor["id"], actor["name"], f"造车组别排期：{result['project']['name']} · {row['stage']}",
                        "；".join(changed), stamp))
        db.commit()
        return {"changed_count": result["changed_count"]}
    except Exception:
        db.rollback()
        raise
