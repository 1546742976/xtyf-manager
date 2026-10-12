"""Repeat-safe collaboration schema migrations."""
from __future__ import annotations

import sqlite3

from common import now_str


COLLABORATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS member_groups (
    member_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
    PRIMARY KEY (member_id, group_id),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (group_id) REFERENCES groups(id)
);
CREATE TABLE IF NOT EXISTS task_groups (
    task_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
    PRIMARY KEY (task_id, group_id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (group_id) REFERENCES groups(id)
);
CREATE TABLE IF NOT EXISTS task_participants (
    task_id INTEGER NOT NULL, member_id INTEGER NOT NULL,
    PRIMARY KEY (task_id, member_id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (member_id) REFERENCES members(id)
);
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id INTEGER NOT NULL, predecessor_id INTEGER NOT NULL,
    PRIMARY KEY (task_id, predecessor_id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (predecessor_id) REFERENCES tasks(id)
);
CREATE TABLE IF NOT EXISTS task_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL, actor_id INTEGER NOT NULL, actor_name TEXT NOT NULL,
    created_at TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
    version INTEGER NOT NULL, reverted_by INTEGER, kind TEXT NOT NULL DEFAULT 'update',
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);
CREATE TABLE IF NOT EXISTS task_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL, original_name TEXT NOT NULL,
    stored_name TEXT UNIQUE NOT NULL, mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
    description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, deleted_at TEXT
);
"""


def _add_column(db: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _catalog_id(db: sqlite3.Connection, table: str, raw_name: str) -> int | None:
    name = raw_name.strip()
    if not name:
        return None
    db.execute(f"INSERT OR IGNORE INTO {table} (name) VALUES (?)", (name,))
    row = db.execute(f"SELECT id FROM {table} WHERE name = ?", (name,)).fetchone()
    return int(row[0])


def migrate(db: sqlite3.Connection) -> None:
    """Apply collaboration schema and legacy relationship migration atomically."""
    db.execute("BEGIN IMMEDIATE")
    try:
        for statement in COLLABORATION_SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        applied = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=1"
        ).fetchone()
        if applied is not None:
            db.commit()
            return
        _add_column(db, "tasks", "project_id INTEGER")
        _add_column(db, "tasks", "parent_id INTEGER")
        _add_column(db, "tasks", "version INTEGER NOT NULL DEFAULT 1")
        _add_column(db, "tasks", "deleted_at TEXT")
        for member_id, group_name in db.execute(
            "SELECT id, group_name FROM members WHERE TRIM(COALESCE(group_name, '')) <> ''"
        ).fetchall():
            group_id = _catalog_id(db, "groups", group_name)
            if group_id is not None:
                db.execute("INSERT OR IGNORE INTO member_groups VALUES (?, ?)", (member_id, group_id))
        for task_id, group_name, project in db.execute(
            "SELECT id, group_name, project FROM tasks"
        ).fetchall():
            group_id = _catalog_id(db, "groups", group_name or "")
            project_id = _catalog_id(db, "projects", project or "")
            if group_id is not None:
                db.execute("INSERT OR IGNORE INTO task_groups VALUES (?, ?)", (task_id, group_id))
            if project_id is not None:
                db.execute("UPDATE tasks SET project_id = COALESCE(project_id, ?) WHERE id = ?", (project_id, task_id))
        db.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, ?)",
            (now_str(),),
        )
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise
