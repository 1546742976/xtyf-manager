"""Validation and graph rules for collaboration tasks."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from modules.task_types import JsonValue

MAX_SQLITE_INTEGER = 9_223_372_036_854_775_807


@dataclass(frozen=True, slots=True)
class InvalidIdError(ValueError):
    label: str

    def __str__(self) -> str:
        return f"{self.label}必须是正整数"


def normalize_ids(value: JsonValue, label: str) -> tuple[list[int] | None, str | None]:
    if value is None:
        return [], None
    if not isinstance(value, list):
        return None, f"{label}必须是 ID 数组"
    result: list[int] = []
    for raw in value:
        if isinstance(raw, bool) or isinstance(raw, float):
            return None, f"{label}包含非法 ID"
        if not isinstance(raw, (int, str)) or (isinstance(raw, str) and not raw.isdigit()):
            return None, f"{label}包含非法 ID"
        try:
            item = int(raw)
        except (TypeError, ValueError):
            return None, f"{label}包含非法 ID"
        if item <= 0 or item > MAX_SQLITE_INTEGER or item in result:
            return None, f"{label}包含重复或非法 ID"
        result.append(item)
    return result, None


def parse_id(value: JsonValue, label: str, nullable: bool = True) -> int | None:
    if isinstance(value, bool) or isinstance(value, float):
        raise InvalidIdError(label)
    if nullable and value in (None, "", 0, "0"):
        return None
    if not isinstance(value, (int, str)) or (isinstance(value, str) and not value.isdigit()):
        raise InvalidIdError(label)
    parsed = int(value)
    if parsed <= 0 or parsed > MAX_SQLITE_INTEGER:
        raise InvalidIdError(label)
    return parsed


def validate_dates(start_date: JsonValue, due_date: JsonValue) -> str | None:
    dates = []
    for label, raw in (("开始日期", start_date), ("截止日期", due_date)):
        if raw is not None and not isinstance(raw, str):
            return f"{label}格式不正确"
        value = (raw or "").strip()
        try:
            dates.append(datetime.strptime(value, "%Y-%m-%d").date() if value else None)
        except ValueError:
            return f"{label}格式不正确（应为 YYYY-MM-DD）"
    if dates[0] and dates[1] and dates[0] > dates[1]:
        return "开始日期不能晚于截止日期"
    return None


def active_ids(db: sqlite3.Connection, table: str, ids: list[int]) -> bool:
    if not ids:
        return True
    placeholders = ",".join("?" for _ in ids)
    predicate = " AND is_active = 1" if table == "members" else ""
    count = db.execute(
        f"SELECT COUNT(*) FROM {table} WHERE id IN ({placeholders}){predicate}", ids
    ).fetchone()[0]
    return count == len(ids)


def valid_tasks(db: sqlite3.Connection, ids: list[int], current_id: int | None = None) -> bool:
    if not ids or (current_id is not None and current_id in ids):
        return not ids or current_id not in ids
    placeholders = ",".join("?" for _ in ids)
    count = db.execute(
        f"SELECT COUNT(*) FROM tasks WHERE id IN ({placeholders}) AND deleted_at IS NULL", ids
    ).fetchone()[0]
    return count == len(ids)


def creates_cycle(
    db: sqlite3.Connection, table: str, edge_column: str, task_id: int, targets: list[int]
) -> bool:
    """Return whether replacing outgoing edges would make a directed cycle."""
    for target in targets:
        frontier = [target]
        visited: set[int] = set()
        while frontier:
            node = frontier.pop()
            if node == task_id:
                return True
            if node in visited:
                continue
            visited.add(node)
            if table == "tasks":
                row = db.execute("SELECT parent_id FROM tasks WHERE id = ?", (node,)).fetchone()
                if row and row[0] is not None:
                    frontier.append(int(row[0]))
            else:
                frontier.extend(int(row[0]) for row in db.execute(
                    f"SELECT {edge_column} FROM {table} WHERE task_id = ?", (node,)
                ).fetchall())
    return False
