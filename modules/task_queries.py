"""Set-based collaboration task read models."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from modules.task_types import JsonObject


def _relation_map(
    db: sqlite3.Connection, table: str, column: str, task_ids: list[int]
) -> dict[int, list[int]]:
    result = {task_id: [] for task_id in task_ids}
    if not task_ids:
        return result
    placeholders = ",".join("?" for _ in task_ids)
    for task_id, value in db.execute(
        f"SELECT task_id,{column} FROM {table} WHERE task_id IN ({placeholders}) ORDER BY {column}",
        task_ids,
    ).fetchall():
        result[int(task_id)].append(int(value))
    return result


def task_data(db: sqlite3.Connection) -> tuple[list[JsonObject], list[JsonObject], list[JsonObject], list[JsonObject], list[JsonObject]]:
    """Load the complete workspace in a constant number of queries."""
    groups = [dict(row) for row in db.execute("SELECT id,name,CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END AS is_active FROM groups ORDER BY name,id").fetchall()]
    projects = [dict(row) for row in db.execute("SELECT id,name,CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END AS is_active FROM projects ORDER BY name,id").fetchall()]
    group_names = {int(group["id"]): str(group["name"]) for group in groups}
    project_names = {int(project["id"]): str(project["name"]) for project in projects}
    member_rows = db.execute(
        "SELECT id,name,username,group_name,role,is_active FROM members ORDER BY group_name,id"
    ).fetchall()
    member_ids = [int(row["id"]) for row in member_rows]
    member_groups = {member_id: [] for member_id in member_ids}
    member_projects = {member_id: [] for member_id in member_ids}
    for member_id, group_id in db.execute(
        "SELECT member_id,group_id FROM member_groups ORDER BY group_id"
    ).fetchall():
        member_groups.setdefault(int(member_id), []).append(int(group_id))
    for member_id, project_id in db.execute(
        "SELECT member_id,project_id FROM member_projects ORDER BY project_id"
    ).fetchall():
        member_projects.setdefault(int(member_id), []).append(int(project_id))
    all_members = [dict(row) | {
        "project_ids": member_projects.get(int(row["id"]), []),
        "project_names": [project_names[x] for x in member_projects.get(int(row["id"]), [])],
        "group_ids": member_groups.get(int(row["id"]), []),
        "group_name": ";".join(group_names[group_id]
                               for group_id in member_groups.get(int(row["id"]), [])),
    } for row in member_rows]
    members = [member for member in all_members if member["is_active"]]
    rows = db.execute(
        "SELECT * FROM tasks WHERE deleted_at IS NULL "
        "ORDER BY CASE WHEN due_date='' THEN 1 ELSE 0 END,due_date,id DESC"
    ).fetchall()
    task_ids = [int(row["id"]) for row in rows]
    group_map = _relation_map(db, "task_groups", "group_id", task_ids)
    participant_map = _relation_map(db, "task_participants", "member_id", task_ids)
    predecessor_map = _relation_map(db, "task_dependencies", "predecessor_id", task_ids)
    # Most recent progress event and its live screenshots are included in board data.
    latest_progress: dict[int, JsonObject] = {}
    if task_ids:
        placeholders = ",".join("?" for _ in task_ids)
        updates = db.execute(
            "SELECT p.id,p.task_id,p.actor_name,p.progress,p.note,p.created_at "
            "FROM task_progress_updates p "
            "WHERE p.task_id IN (" + placeholders + ") "
            "AND p.id IN (SELECT MAX(id) FROM task_progress_updates GROUP BY task_id)",
            task_ids,
        ).fetchall()
        for update in updates:
            latest_progress[int(update["task_id"])] = dict(update) | {"image_ids": []}
        if updates:
            update_ids = [int(update["id"]) for update in updates]
            image_placeholders = ",".join("?" for _ in update_ids)
            for image in db.execute(
                "SELECT progress_update_id,id FROM task_attachments "
                "WHERE progress_update_id IN (" + image_placeholders + ") AND deleted_at IS NULL "
                "ORDER BY id", update_ids,
            ).fetchall():
                for event in latest_progress.values():
                    if int(event["id"]) == int(image["progress_update_id"]):
                        event["image_ids"].append(int(image["id"]))
    by_id = {int(row["id"]): dict(row) for row in rows}
    today = datetime.now().date()
    tasks: list[JsonObject] = []
    for row in rows:
        task = dict(row)
        task_id = int(task["id"])
        due = str(task.get("due_date") or "").strip()
        try:
            task["days_left"] = (datetime.strptime(due, "%Y-%m-%d").date() - today).days if due else None
        except ValueError:
            task["days_left"] = None
        task["group_ids"] = group_map[task_id]
        task["group_name"] = ";".join(group_names[group_id] for group_id in group_map[task_id])
        project_id = task.get("project_id")
        task["project"] = project_names.get(int(project_id), "") if project_id is not None else ""
        task["participant_ids"] = participant_map[task_id]
        task["predecessor_ids"] = predecessor_map[task_id]
        task["latest_progress"] = latest_progress.get(task_id)
        warnings: list[str] = []
        predecessor_dates: list[str] = []
        dates_complete = bool(predecessor_map[task_id])
        for predecessor_id in predecessor_map[task_id]:
            predecessor = by_id.get(predecessor_id)
            if predecessor is None:
                dates_complete = False
                warnings.append("前置任务不存在或已删除")
                continue
            if predecessor["status"] == "已取消":
                warnings.append(f"前置任务“{predecessor['title']}”已取消")
            elif predecessor["status"] != "已完成":
                warnings.append(f"前置任务“{predecessor['title']}”尚未完成")
            pred_due = str(predecessor.get("due_date") or "")
            if pred_due:
                predecessor_dates.append(pred_due)
                if task.get("start_date") and str(task["start_date"]) < pred_due:
                    warnings.append(f"开始日期早于前置任务“{predecessor['title']}”截止日期")
            else:
                dates_complete = False
                warnings.append(f"前置任务“{predecessor['title']}”未设置截止日期")
        task["suggested_start_date"] = max(predecessor_dates) if dates_complete and predecessor_dates else None
        task["dependency_warnings"] = warnings
        tasks.append(task)
    return members, all_members, groups, projects, tasks
