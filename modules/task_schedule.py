"""Dependency-aware previews and atomic, explicitly accepted task scheduling."""
from __future__ import annotations

import hashlib
import heapq
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer

from common import now_str
from modules.task_dates import completion_date, dependency_boundary
from modules.task_mutations import _revision
from modules.task_validation import parse_id

STAGES = ("机械", "电控", "视觉")
SCHEDULE_STATUSES = ("未开始", "进行中", "阻塞")
ALGORITHM_VERSION = 2


class ScheduleError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def resolve_stage(group_names: list[str], saved: str | None) -> tuple[list[str], str | None]:
    """Legacy display classification; specialties never constrain scheduling."""
    options = [stage for stage in STAGES if stage in group_names]
    stage = options[0] if len(options) == 1 else saved if saved in options else None
    return options, stage


def _date(value: object) -> date | None:
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat() == value else None
    except (ValueError, TypeError):
        return None


def _load(db: sqlite3.Connection, project_id: int) -> dict:
    project = db.execute("SELECT id,name,deleted_at FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None or project["deleted_at"] is not None:
        raise ScheduleError("造车组别不存在或已删除", 404)
    # Traverse all referenced ancestors, including external and deleted tasks.
    # Their fields and edges belong to the signed snapshot too.
    catalog = {row["id"]: dict(row) for row in db.execute(
        "SELECT t.id,t.title,t.owner_id,t.project_id,t.parent_id,t.status,t.start_date,t.due_date,"
        "t.completed_at,t.start_date_origin,t.schedule_stage,t.schedule_locked,t.version,t.deleted_at,"
        "p.name AS project_name,p.deleted_at AS project_deleted_at FROM tasks t LEFT JOIN projects p ON p.id=t.project_id ORDER BY t.id"
    )}
    edges: dict[int, set[int]] = {}
    for task_id, predecessor_id in db.execute("SELECT task_id,predecessor_id FROM task_dependencies ORDER BY task_id,predecessor_id"):
        edges.setdefault(task_id, set()).add(predecessor_id)
    for task in catalog.values():
        if task["parent_id"] is not None:
            edges.setdefault(task["id"], set()).add(task["parent_id"])
    local_ids = [tid for tid, task in catalog.items() if task["project_id"] == project_id and task["deleted_at"] is None]
    relevant, pending = set(), list(local_ids)
    while pending:
        task_id = pending.pop()
        if task_id not in relevant:
            relevant.add(task_id)
            pending.extend(edges.get(task_id, ()))
    tasks = [catalog[tid] for tid in sorted(relevant) if tid in catalog]
    local_owners = {catalog[tid]["owner_id"] for tid in local_ids
                    if catalog[tid]["status"] in SCHEDULE_STATUSES and catalog[tid]["owner_id"] is not None}
    # Include every active external task for these owners, even if it does not
    # overlap yet. A new assignment or changed date must invalidate the preview.
    # Keep these separate from ancestors: they supply workload warnings only.
    owner_tasks = [task for task in catalog.values()
                   if task["project_id"] != project_id and task["deleted_at"] is None
                   and task["status"] in SCHEDULE_STATUSES and task["owner_id"] in local_owners]
    owners = [dict(row) for row in db.execute(
        "SELECT DISTINCT m.id,m.name,m.is_active,m.deleted_at FROM members m "
        "JOIN tasks t ON t.owner_id=m.id WHERE t.project_id=? AND t.deleted_at IS NULL ORDER BY m.id", (project_id,)
    )]
    memberships = [dict(row) for row in db.execute(
        "SELECT mg.member_id,g.id,g.name,g.deleted_at FROM member_groups mg JOIN groups g ON g.id=mg.group_id "
        "WHERE mg.member_id IN (SELECT owner_id FROM tasks WHERE project_id=? AND deleted_at IS NULL) "
        "ORDER BY mg.member_id,g.id", (project_id,)
    )]
    return {"project": dict(project), "tasks": tasks, "local_ids": local_ids,
            "dependencies": {str(tid): sorted(edges.get(tid, ())) for tid in sorted(relevant)},
            "owners": owners, "memberships": memberships, "owner_tasks": owner_tasks}


def _digest(source: dict) -> str:
    return hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(current_app.secret_key, salt="vehicle-task-schedule-v2")


def _plan(source: dict, request: object) -> dict:
    if not isinstance(request, dict) or type(request.get("algorithm_version")) is not int or request["algorithm_version"] != ALGORITHM_VERSION:
        raise ScheduleError("排期规则已更新，请刷新页面后重新预览")
    base_raw = request.get("base_date", datetime.now(timezone(timedelta(hours=8))).date().isoformat())
    base = _date(base_raw)
    if base is None:
        raise ScheduleError("基准日期必须为 YYYY-MM-DD 格式的有效日期")
    tasks = {task["id"]: task for task in source["tasks"]}
    local = {tid: tasks[tid] for tid in source["local_ids"]}
    eligible = {tid for tid, task in local.items() if task["status"] in SCHEDULE_STATUSES}
    errors, warnings = [], []

    def warning(task_id, message, **extra):
        warnings.append({"task_id": task_id, "message": message, **extra})

    if "task_ids" not in request:
        selected = {tid for tid in eligible if local[tid]["status"] != "进行中" and not local[tid]["schedule_locked"]}
    else:
        if not isinstance(request["task_ids"], list):
            raise ScheduleError("参与排期任务必须为 ID 数组")
        selected = set()
        for raw in request["task_ids"]:
            try:
                tid = parse_id(raw, "任务 ID", False)
            except ValueError as exc:
                raise ScheduleError("参与排期任务包含非法 ID") from exc
            if tid in eligible:
                selected.add(tid)
            else:
                warning(tid, "该任务已失效或不再参与排期，已从本次选择中移除")
    raw_durations = request.get("durations", {})
    if not isinstance(raw_durations, dict):
        raise ScheduleError("补充工期必须是任务 ID 到天数的对象")
    supplied = {}
    for key, value in raw_durations.items():
        try:
            tid = parse_id(key, "任务 ID", False)
        except ValueError as exc:
            raise ScheduleError("补充工期包含非法任务 ID") from exc
        if tid not in eligible:
            warning(tid, "该任务已失效或不再参与排期，已移除其补充工期")
            continue
        if type(value) is not int or value <= 0:
            raise ScheduleError(f"#{tid} 的工期必须是正整数天数")
        supplied[str(tid)] = value

    owners = {owner["id"]: owner for owner in source["owners"]}
    names: dict[int, list[str]] = {}
    for membership in source["memberships"]:
        if membership["deleted_at"] is None:
            names.setdefault(membership["member_id"], []).append(membership["name"])
    rows = {}
    for tid, task in local.items():
        options, stage = resolve_stage(names.get(task["owner_id"], []), task["schedule_stage"])
        start, end = _date(task["start_date"]), _date(task["due_date"])
        duration = (end - start).days + 1 if start and end and start <= end else None
        needs_duration = duration is None
        if duration is not None and str(tid) in supplied:
            supplied.pop(str(tid))
            warning(tid, "已有完整有效日期，沿用原工期并移除补充工期")
        duration = duration or supplied.get(str(tid))
        protected = tid in eligible and (task["status"] == "进行中" or bool(task["schedule_locked"]))
        reason = "、".join(label for condition, label in ((task["status"] == "进行中", "进行中"),
                                                        (bool(task["schedule_locked"]), "自动排期固定日期")) if condition)
        state = "unchanged" if tid in selected else "protected" if protected else "excluded"
        note = "保持原日期" if tid in selected else "受保护，保留原日期" if protected else "本次不参与，保留原日期"
        rows[tid] = {**task, "owner_name": owners.get(task["owner_id"], {}).get("name", "未指定"),
                     "stage_options": options, "stage": stage, "eligible": tid in eligible,
                     "selected": tid in selected, "protected": protected, "protection_reason": reason,
                     "before": {key: task[key] for key in ("start_date", "due_date")},
                     "after": {key: task[key] for key in ("start_date", "due_date")},
                     "duration_days": duration, "needs_duration": tid in eligible and needs_duration,
                     "shift_days": 0, "state": state, "note": note, "predecessors": []}

    invalid = set()

    def problem(tid, message, needs_duration=False):
        invalid.add(tid)
        if tid in rows and tid in eligible:
            error = {"task_id": tid, "message": message}
            if error not in errors:
                errors.append(error)
            rows[tid]["state"] = "needs_duration" if needs_duration else "conflict"
            rows[tid]["note"] = message

    deps = {int(tid): ids for tid, ids in source["dependencies"].items()}
    # Kahn's algorithm avoids recursion limits on long project histories.
    indegrees = {tid: sum(pid in tasks for pid in deps.get(tid, [])) for tid in tasks}
    children: dict[int, list[int]] = {}
    for tid in tasks:
        for pid in deps.get(tid, []):
            children.setdefault(pid, []).append(tid)
    ready = [tid for tid, degree in indegrees.items() if degree == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        tid = heapq.heappop(ready)
        ordered.append(tid)
        for child in children.get(tid, []):
            indegrees[child] -= 1
            if indegrees[child] == 0:
                heapq.heappush(ready, child)
    for tid, degree in indegrees.items():
        if degree:
            problem(tid, "存在依赖环，或前置处于依赖环中；请先修正依赖")

    effective = {tid: dict(task) for tid, task in tasks.items()}
    for tid in ordered:
        task = tasks[tid]
        if task["status"] in ("已完成", "已取消"):
            continue
        bounds = []
        for pid in deps.get(tid, []):
            predecessor = effective.get(pid)
            if (predecessor is None or predecessor["deleted_at"] is not None or predecessor["status"] == "已取消"
                    or predecessor["project_deleted_at"] is not None):
                problem(tid, f"前置 #{pid} 不存在、已删除、已取消或所属车组已归档")
                continue
            if pid in invalid:
                problem(tid, f"前置 #{pid} 尚有排期冲突，请先解决其问题")
                continue
            boundary = dependency_boundary(predecessor)
            if boundary is None:
                problem(tid, f"前置 #{pid} 缺少有效日期，无法计算衔接日期")
                continue
            bounds.append((pid, _date(boundary)))
            if tid in rows and predecessor["status"] == "已完成" and completion_date(predecessor["completed_at"]) is None:
                warning(tid, f"已完成前置 #{pid} 缺少实际完成日期，暂按计划截止日期衔接", related_task_ids=[pid])
        # External ancestors are fixed too. Validate their dependency bounds
        # after any local predecessors move, then propagate conflicts downstream.
        if tid not in selected:
            if bounds:
                current_start = _date(task["start_date"])
                if current_start is None:
                    problem(tid, "未参与调整的任务缺少有效开始日期，无法满足前置约束")
                elif current_start < max(bound for _, bound in bounds):
                    problem(tid, "固定或未参与调整的日期早于前置允许的日期；请允许本次调整或修改依赖")
            continue
        row = rows[tid]
        if row["duration_days"] is None:
            problem(tid, "请填写正整数工期，以补齐或修正日期", needs_duration=True)
            continue
        if tid in invalid:
            continue
        start_candidates = [base] + [bound for _, bound in bounds]
        old_start, old_end = _date(task["start_date"]), _date(task["due_date"])
        if old_start:
            start_candidates.append(old_start)
        try:
            span = timedelta(days=row["duration_days"] - 1)
            if old_end:
                # A saved end date is a lower bound even when start was missing.
                start_candidates.append(old_end - span if (old_end - date.min) >= span else date.min)
            start = max(start_candidates)
            end = start + span
        except OverflowError:
            problem(tid, "工期或调整后的日期超出有效日期范围")
            continue
        row["after"] = {"start_date": start.isoformat(), "due_date": end.isoformat()}
        row["shift_days"] = (start - old_start).days if old_start else None
        row["state"] = "changed" if row["before"] != row["after"] else "unchanged"
        if row["state"] == "changed":
            causes = []
            if bounds and max(bound for _, bound in bounds) == start:
                causes.append("满足前置衔接")
            if base == start:
                causes.append("不早于基准日期")
            if str(tid) in supplied:
                causes.append("按补充工期计算")
            row["note"] = "；".join(causes or ["保留已有日期下界，保持原工期"])
        if row["protected"]:
            row["note"] += "；本次已允许调整，长期固定标记保留"
        effective[tid].update(row["after"])

    for tid, row in rows.items():
        for pid in deps.get(tid, []):
            pred = effective.get(pid, {})
            row["predecessors"].append({"id": pid, "title": pred.get("title", "失效任务"),
                "external": pred.get("project_id") != source["project"]["id"], "status": pred.get("status"),
                "boundary_date": dependency_boundary(pred) if pred and pid not in invalid else None})

    # A conflict elsewhere must not hide useful preview changes or warnings.
    owner_rows: dict[int, list[dict]] = {}
    for row in rows.values():
        if row["eligible"] and row["owner_id"] is not None:
            owner_rows.setdefault(row["owner_id"], []).append(row)
    for task in source["owner_tasks"]:
        owner_rows.setdefault(task["owner_id"], []).append({**task,
            "after": {key: task[key] for key in ("start_date", "due_date")}})
    for peers in owner_rows.values():
        for index, left in enumerate(peers):
            if left["id"] not in rows:
                continue
            a, b = (_date(left["after"][key]) for key in ("start_date", "due_date"))
            if a is None or b is None or a > b:
                continue
            for right in peers[index + 1:]:
                c, d = (_date(right["after"][key]) for key in ("start_date", "due_date"))
                if c and d and c <= d and max(a, c) <= min(b, d):
                    overlap_start, overlap_end = max(a, c).isoformat(), min(b, d).isoformat()
                    related = [{"id": item["id"], "title": item["title"], "project_id": item["project_id"],
                                "project_name": item["project_name"] or "未归属造车组别",
                                "external": item["id"] not in rows, **item["after"]} for item in (left, right)]
                    warning(left["id"], f"同一负责人任务 #{left['id']} 与 #{right['id']} 日期重叠"
                            f"（{overlap_start} 至 {overlap_end}，含首尾），请核对工作量",
                            kind="owner_overlap", owner_id=left["owner_id"],
                            related_task_ids=[left["id"], right["id"]], related_tasks=related,
                            overlap_start=overlap_start, overlap_end=overlap_end)
    result_rows = [rows[tid] for tid in ordered if tid in rows] + [rows[tid] for tid in sorted(rows) if indegrees[tid]]
    changed = sum(row["before"] != row["after"] for row in rows.values() if row["selected"])
    return {"algorithm_version": ALGORITHM_VERSION,
            "project": {"id": source["project"]["id"], "name": source["project"]["name"]},
            "base_date": base.isoformat(), "task_ids": sorted(selected), "durations": supplied,
            "rows": result_rows, "errors": errors, "warnings": warnings,
            "changed_count": changed, "can_apply": bool(selected) and not errors}


def preview_schedule(db: sqlite3.Connection, project_id: int, request: object) -> dict:
    db.execute("BEGIN")
    try:
        source = _load(db, project_id)
        result = _plan(source, request)
        parameters = {key: result[key] for key in ("algorithm_version", "base_date", "task_ids", "durations")}
        result["snapshot"] = _signer().dumps({"project_id": project_id, "digest": _digest(source),
                                             "parameters": parameters}) if result["can_apply"] else None
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
        raise ScheduleError("排期规则或预览已失效，请刷新页面并重新预览") from exc
    if not isinstance(expected, dict) or expected.get("project_id") != project_id:
        raise ScheduleError("排期预览与造车组别不匹配")
    db.execute("BEGIN IMMEDIATE")
    try:
        source = _load(db, project_id)
        if expected.get("digest") != _digest(source):
            raise ScheduleError("任务、前置或负责人跨车组任务信息已变化，请重新预览；本次未保存任何调整", 409)
        result = _plan(source, expected.get("parameters"))
        if not result["can_apply"]:
            raise ScheduleError("排期条件不完整，请重新预览")
        stamp = now_str()
        for row in result["rows"]:
            if not row["selected"] or row["before"] == row["after"]:
                continue
            before, after = dict(row["before"]), dict(row["after"])
            before["start_date_origin"] = row["start_date_origin"]
            after["start_date_origin"] = "schedule"
            changed = [key for key in before if before[key] != after[key]]
            version = row["version"] + 1
            cur = db.execute(
                "UPDATE tasks SET start_date=?,due_date=?,start_date_origin='schedule',updated_at=?,version=? WHERE id=? AND version=?",
                (after["start_date"], after["due_date"], stamp, version, row["id"], row["version"]),
            )
            if cur.rowcount != 1:
                raise ScheduleError("任务已被其他人修改，请重新预览", 409)
            _revision(db, row["id"], actor, {key: before[key] for key in changed},
                      {key: after[key] for key in changed}, version)
            db.execute("INSERT INTO logs(task_id,member_id,member_name,note,changes,created_at) VALUES(?,?,?,?,?,?)",
                       (row["id"], actor["id"], actor["name"], f"造车组别依赖排期：{result['project']['name']}",
                        "；".join(changed), stamp))
        db.commit()
        return {"changed_count": result["changed_count"]}
    except Exception:
        db.rollback()
        raise
