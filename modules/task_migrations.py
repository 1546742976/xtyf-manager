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
CREATE TABLE IF NOT EXISTS member_projects (
    member_id INTEGER NOT NULL, project_id INTEGER NOT NULL,
    PRIMARY KEY (member_id, project_id),
    FOREIGN KEY (member_id) REFERENCES members(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
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
CREATE TABLE IF NOT EXISTS task_progress_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
    actor_id INTEGER NOT NULL, actor_name TEXT NOT NULL,
    progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100),
    note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_task_progress_updates_task ON task_progress_updates(task_id,id);
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


DEFAULT_GROUPS = ("视觉", "电控", "机械", "硬件", "宣传", "运营")
DEFAULT_VEHICLE_GROUPS = ("步兵", "哨兵", "重装", "飞镖")


def _upgrade_composite_groups(db: sqlite3.Connection) -> None:
    """Split historical semicolon-combined group labels into separate relations."""
    for group_id, raw_name in db.execute(
        "SELECT id, name FROM groups WHERE instr(name, ';') > 0"
    ).fetchall():
        names = list(dict.fromkeys(part.strip() for part in raw_name.split(";") if part.strip()))
        if len(names) < 2:
            continue
        new_ids = [_catalog_id(db, "groups", name) for name in names]
        for relation, owner_column in (("member_groups", "member_id"), ("task_groups", "task_id")):
            owners = [row[0] for row in db.execute(
                f"SELECT {owner_column} FROM {relation} WHERE group_id=?", (group_id,)
            ).fetchall()]
            db.executemany(
                f"INSERT OR IGNORE INTO {relation} ({owner_column}, group_id) VALUES (?, ?)",
                ((owner_id, new_id) for owner_id in owners for new_id in new_ids),
            )
            db.execute(f"DELETE FROM {relation} WHERE group_id=?", (group_id,))
        db.execute("DELETE FROM groups WHERE id=?", (group_id,))


def migrate(db: sqlite3.Connection) -> None:
    """Apply schema and versioned data upgrades atomically and repeat-safely."""
    db.execute("BEGIN IMMEDIATE")
    try:
        for statement in COLLABORATION_SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        # Catalog archiving preserves historical task and member relationships.
        _add_column(db, "groups", "deleted_at TEXT")
        _add_column(db, "projects", "deleted_at TEXT")
        _add_column(db, "task_attachments", "progress_update_id INTEGER")
        applied_v1 = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=1"
        ).fetchone()
        if applied_v1 is None:
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
            db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, ?)", (now_str(),))
        # Seed exactly once so future administrator renames never get reverted.
        if db.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone() is None:
            _upgrade_composite_groups(db)
            db.executemany("INSERT OR IGNORE INTO groups(name) VALUES (?)",
                           ((name,) for name in DEFAULT_GROUPS))
            db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (2, ?)", (now_str(),))
        # A task already has one project_id. Projects are the stable vehicle-group
        # dictionary; members can now participate in several through member_projects.
        if db.execute("SELECT 1 FROM schema_migrations WHERE version=3").fetchone() is None:
            db.executemany(
                "INSERT OR IGNORE INTO projects(name) VALUES (?)",
                ((name,) for name in DEFAULT_VEHICLE_GROUPS),
            )
            # Preserve existing project labels (e.g. 轮腿步兵/狗洞步兵), and infer
            # participation only from actual assigned tasks; do not infer from specialty.
            db.execute(
                "INSERT OR IGNORE INTO member_projects(member_id, project_id) "
                "SELECT owner_id, project_id FROM tasks "
                "WHERE owner_id IS NOT NULL AND project_id IS NOT NULL"
            )
            db.execute(
                "INSERT OR IGNORE INTO member_projects(member_id, project_id) "
                "SELECT tp.member_id, t.project_id FROM task_participants tp "
                "JOIN tasks t ON t.id=tp.task_id WHERE t.project_id IS NOT NULL"
            )
            db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (3, ?)", (now_str(),))
        if db.execute("SELECT 1 FROM schema_migrations WHERE version=4").fetchone() is None:
            _add_column(db, "tasks", "schedule_stage TEXT")
            db.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (4, ?)", (now_str(),))
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise
