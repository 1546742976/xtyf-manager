"""One-time date suggestions; saved task dates never cascade from dependencies."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta


def dependency_start_date(due_date: str | None) -> str | None:
    """Use calendar arithmetic, without a timezone or millisecond offset."""
    try:
        day = date.fromisoformat(due_date or "")
        if day.isoformat() != due_date:
            return None
        return (day - timedelta(days=2)).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def parent_start_date(db: sqlite3.Connection, parent_id: int | None) -> str | None:
    if parent_id is None:
        return None
    row = db.execute("SELECT due_date FROM tasks WHERE id=? AND deleted_at IS NULL", (parent_id,)).fetchone()
    return dependency_start_date(row[0]) if row else None


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
            return "主前置任务未设置有效截止日期，无法重置开始日期"
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
