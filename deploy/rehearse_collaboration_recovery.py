#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["flask>=3.0"]
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly from the repository root (no production data is read):
#      uv run deploy/rehearse_collaboration_recovery.py
# 3. The JSON report is written under .omo/evidence/todo-collaboration/recovery/.
# ──────────────────
"""Exercise collaboration migration and joint database/file recovery on synthetic data."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPOSITORY_ROOT / ".omo/evidence/todo-collaboration/recovery/rehearsal-report.json"
sys.path.insert(0, str(REPOSITORY_ROOT))

from modules.task_migrations import migrate


@dataclass(frozen=True, slots=True)
class RehearsalResult:
    """Boolean results and non-sensitive fixture counts for the recovery rehearsal."""

    synthetic_members: int
    synthetic_tasks: int
    synthetic_logs: int
    legacy_content_preserved: bool
    migration_second_run_idempotent: bool
    removed_membership_not_reintroduced: bool
    mid_migration_failure_rolled_back: bool
    sqlite_online_backup_verified: bool
    attachment_backup_verified: bool
    joint_restore_verified: bool
    restored_integrity_check_ok: bool
    temporary_fixture_removed: bool


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    """Open an isolated database with deterministic rows and foreign keys enabled."""
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise
    finally:
        db.close()


def _create_legacy_database(path: Path, *, omit_task_group: bool = False) -> None:
    """Create a pre-collaboration fixture; the broken variant forces a late migration error."""
    task_group = "" if omit_task_group else "group_name TEXT DEFAULT '',"
    with _connect(path) as db:
        db.executescript(
            f"""
            CREATE TABLE members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, group_name TEXT DEFAULT '',
                role TEXT NOT NULL DEFAULT 'member', is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL, detail TEXT DEFAULT '', owner_id INTEGER,
                {task_group} project TEXT DEFAULT '', priority TEXT DEFAULT '中',
                status TEXT DEFAULT '未开始', progress INTEGER DEFAULT 0,
                start_date TEXT DEFAULT '', due_date TEXT DEFAULT '', created_by INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
                member_id INTEGER, member_name TEXT DEFAULT '', note TEXT DEFAULT '',
                changes TEXT DEFAULT '', created_at TEXT NOT NULL
            );
            """
        )
        if omit_task_group:
            return
        db.executemany(
            "INSERT INTO members (name, username, password_hash, group_name, role, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("演练成员甲", "fixture_active", "fixture-hash-active", "  视觉;电控  ", "member", 1, "2026-10-12 00:00:00"),
                ("演练成员乙", "fixture_inactive", "fixture-hash-inactive", " 电控 ", "member", 0, "2026-10-12 00:00:01"),
            ],
        )
        db.executemany(
            "INSERT INTO tasks (title, detail, owner_id, group_name, project, priority, status, progress, "
            "start_date, due_date, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("演练任务甲", "保留原始内容", 1, "  机械  ", " 哨兵 ", "高", "进行中", 0, "2026-10-13", "2026-10-20", 1, "2026-10-12 00:01:00", "2026-10-12 00:01:00"),
                ("演练任务乙", "关联停用成员的历史任务", 2, "电控", " 哨兵 ", "中", "未开始", 0, "", "", 1, "2026-10-12 00:02:00", "2026-10-12 00:02:00"),
            ],
        )
        db.executemany(
            "INSERT INTO logs (task_id, member_id, member_name, note, changes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, 1, "演练成员甲", "建立合成任务", "{}", "2026-10-12 00:03:00"),
                (2, 2, "演练成员乙", "保留合成历史", "{}", "2026-10-12 00:04:00"),
            ],
        )


def _digest_rows(db: sqlite3.Connection, query: str) -> str:
    """Hash deterministic rows internally without emitting fixture hash or password values."""
    rows = [tuple(row) for row in db.execute(query).fetchall()]
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _legacy_digest(db: sqlite3.Connection) -> str:
    """Summarize every legacy table field that a migration must preserve exactly."""
    statements = (
        "SELECT id, name, username, password_hash, group_name, role, is_active, created_at FROM members ORDER BY id",
        "SELECT id, title, detail, owner_id, group_name, project, priority, status, progress, start_date, due_date, created_by, created_at, updated_at FROM tasks ORDER BY id",
        "SELECT id, task_id, member_id, member_name, note, changes, created_at FROM logs ORDER BY id",
    )
    joined = "|".join(_digest_rows(db, statement) for statement in statements)
    return hashlib.sha256(joined.encode("ascii")).hexdigest()


def _attachment_digest(root: Path) -> str:
    """Hash relative filenames and contents deterministically without exposing attachment bytes."""
    entries = [
        f"{item.relative_to(root).as_posix()}:{hashlib.sha256(item.read_bytes()).hexdigest()}"
        for item in sorted(root.rglob("*"))
        if item.is_file()
    ]
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _integrity_ok(db: sqlite3.Connection) -> bool:
    """Return whether SQLite itself accepts the restored database structure and pages."""
    return db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def _require(condition: bool, description: str) -> None:
    """Stop the rehearsal at the first failed safety invariant."""
    if not condition:
        raise RuntimeError(description)


def _exercise_rollback(path: Path) -> bool:
    """Prove a late legacy-schema failure rolls DDL, new columns, and seeded relations back."""
    _create_legacy_database(path, omit_task_group=True)
    with _connect(path) as db:
        try:
            migrate(db)
        except sqlite3.OperationalError:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            task_columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            return (
                "schema_migrations" not in tables
                and "groups" not in tables
                and "project_id" not in task_columns
                and "parent_id" not in task_columns
                and "version" not in task_columns
                and "deleted_at" not in task_columns
            )
    return False


def _run_rehearsal() -> RehearsalResult:
    """Run migration, repeat migration, online backup, mutation, and joint isolated restoration."""
    with tempfile.TemporaryDirectory(prefix="xtyf-collaboration-recovery-") as temporary_name:
        temporary_root = Path(temporary_name)
        source_db_path = temporary_root / "legacy-progress.db"
        attachment_root = temporary_root / "source-attachments"
        attachment_root.mkdir()
        (attachment_root / "task-1").mkdir()
        (attachment_root / "task-1" / "progress.png").write_bytes(b"synthetic-png-fixture")
        (temporary_root / "secret.key").write_text("synthetic-temporary-secret\n", encoding="utf-8")
        _create_legacy_database(source_db_path)

        with _connect(source_db_path) as source:
            baseline_digest = _legacy_digest(source)
            migrate(source)
            migrated_digest = _legacy_digest(source)
            _require(baseline_digest == migrated_digest, "legacy rows changed during migration")
            source.execute("INSERT INTO groups (name) VALUES ('演练额外组')")
            extra_group_id = int(source.execute("SELECT id FROM groups WHERE name='演练额外组'").fetchone()[0])
            source.execute("INSERT INTO member_groups (member_id, group_id) VALUES (1, ?)", (extra_group_id,))
            source.execute("DELETE FROM member_groups WHERE member_id=1 AND group_id=?", (extra_group_id,))
            source.commit()
            migrate(source)
            preserved_digest = _legacy_digest(source)
            removed_membership = source.execute(
                "SELECT 1 FROM member_groups WHERE member_id=1 AND group_id=?", (extra_group_id,)
            ).fetchone()
            _require(removed_membership is None, "repeated migration restored removed membership")
            _require(
                source.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=1").fetchone()[0] == 1,
                "migration version was not singular after second run",
            )
            backup_path = temporary_root / "backup-progress.db"
            with _connect(backup_path) as backup:
                source.backup(backup)
                backup_digest = _legacy_digest(backup)
            snapshot_attachment_root = temporary_root / "backup-attachments"
            shutil.copytree(attachment_root, snapshot_attachment_root)
            attachment_before_mutation = _attachment_digest(snapshot_attachment_root)
            source.execute("UPDATE tasks SET title='已破坏的合成任务' WHERE id=1")
            source.commit()

        (attachment_root / "task-1" / "progress.png").unlink()
        (attachment_root / "task-1" / "mutated.webp").write_bytes(b"mutated-synthetic-attachment")
        restored_root = temporary_root / "restored"
        restored_root.mkdir()
        restored_db_path = restored_root / "progress.db"
        with _connect(backup_path) as backup, _connect(restored_db_path) as restored:
            backup.backup(restored)
            restored_digest = _legacy_digest(restored)
            restored_integrity = _integrity_ok(restored)
        restored_attachment_root = restored_root / "attachments"
        shutil.copytree(snapshot_attachment_root, restored_attachment_root)
        restored_attachment_digest = _attachment_digest(restored_attachment_root)
        legacy_counts = (2, 2, 2)
        _require(restored_digest == preserved_digest == backup_digest, "restored legacy content differs from backup")
        _require(restored_attachment_digest == attachment_before_mutation, "restored attachment tree differs from backup")
        _require(restored_integrity, "restored SQLite integrity_check failed")
        rollback_ok = _exercise_rollback(temporary_root / "rollback-proof.db")
        _require(rollback_ok, "mid-migration failure left partial schema or relations")

    return RehearsalResult(
        synthetic_members=legacy_counts[0],
        synthetic_tasks=legacy_counts[1],
        synthetic_logs=legacy_counts[2],
        legacy_content_preserved=True,
        migration_second_run_idempotent=True,
        removed_membership_not_reintroduced=True,
        mid_migration_failure_rolled_back=True,
        sqlite_online_backup_verified=True,
        attachment_backup_verified=True,
        joint_restore_verified=True,
        restored_integrity_check_ok=True,
        temporary_fixture_removed=not temporary_root.exists(),
    )


def main() -> None:
    """Execute the isolated rehearsal and write its non-sensitive machine-readable report."""
    result = _run_rehearsal()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "scenario": "synthetic legacy migration plus database-and-attachment recovery",
        "command_executed_from_workspace_root": "uv run deploy/rehearse_collaboration_recovery.py",
        "interpreter_used": sys.executable,
        "production_data_read": False,
        "real_secret_read": False,
        "synthetic_fixture": "two named members (one active, one inactive), two tasks, two logs, one dummy attachment, and one temporary dummy secret",
        "mutation_before_restore": "task title changed; original dummy attachment removed; replacement dummy attachment added",
        "fixture_teardown": "TemporaryDirectory removed all generated database, attachment, and temporary-key files.",
        "result": asdict(result),
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rehearsal passed; report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
