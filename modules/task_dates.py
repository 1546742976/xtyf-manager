"""One-time date suggestions; saved task dates never cascade from dependencies."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta


def parse_calendar_date(value: object) -> date | None:
    """Accept an exact ISO calendar date rather than a loosely parsed label."""
    if not isinstance(value, str):
        return None
    try:
        day = date.fromisoformat(value)
        return day if day.isoformat() == value else None
    except ValueError:
        return None


def completion_date(value: object) -> date | None:
    """Read the saved local completion day, validating the entire timestamp."""
    if not isinstance(value, str):
        return None
    day = parse_calendar_date(value[:10])
    if day is None:
        return None
    if len(value) == 10:
        return day
    if value[10:11] not in (" ", "T"):
        return None
    try:
        datetime.fromisoformat(value)
        return day
    except ValueError:
        return None


def dependency_boundary(task: dict) -> str | None:
    """Earliest successor start: two days before the end, but never before start.

    Completed tasks use their actual completion day when known. Older completed
    tasks fall back to the planned due date without fabricating an actual date.
    """
    end = completion_date(task.get("completed_at")) if task.get("status") == "已完成" else None
    end = end or parse_calendar_date(task.get("due_date"))
    if end is None:
        return None
    try:
        boundary = end - timedelta(days=2)
    except OverflowError:
        return None
    start = parse_calendar_date(task.get("start_date"))
    return max(start, boundary).isoformat() if start else boundary.isoformat()


def dependency_start_date(due_date: str | None) -> str | None:
    """Use calendar arithmetic, without a timezone or millisecond offset."""
    return dependency_boundary({"due_date": due_date})


def parent_start_date(db: sqlite3.Connection, parent_id: int | None) -> str | None:
    if parent_id is None:
        return None
    row = db.execute(
        "SELECT start_date,due_date,status,completed_at FROM tasks WHERE id=? AND deleted_at IS NULL",
        (parent_id,),
    ).fetchone()
    return dependency_boundary(dict(zip(("start_date", "due_date", "status", "completed_at"), row))) if row else None


def apply_start_date_request(db, data: dict, values: dict, prior: dict | None = None) -> str | None:
    """Apply an explicit reset or an unsaved default, preserving date provenance.

    A stored dependency origin records where the date came from, not a live
    subscription. Subsequent predecessor edits do not write the dependent task.
    """
    if "reset_start_date" in data and type(data["reset_start_date"]) is not bool:
        return "重置开始日期必须为布尔值"
    if "start_date_origin" in data and data["start_date_origin"] != "manual":
        return "日期来源只能显式标记为手动设置"
    if data.get("reset_start_date"):
        suggested = parent_start_date(db, values.get("parent_id"))
        if suggested is None:
            return "主前置任务缺少有效完成日期或截止日期，无法重置开始日期"
        values["start_date"] = suggested
        values["start_date_origin"] = "dependency"
    elif data.get("start_date_origin") == "manual":
        values["start_date_origin"] = "manual"
    elif prior is None:
        if values["start_date"]:
            values["start_date_origin"] = "manual"
        else:
            suggested = parent_start_date(db, values.get("parent_id"))
            values["start_date"] = suggested or ""
            values["start_date_origin"] = "dependency" if suggested else "frozen"
    elif values["start_date"] != prior["start_date"]:
        values["start_date_origin"] = "manual"
    return None
