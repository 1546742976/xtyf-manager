"""Transactional, repeat-safe collaboration migration tests."""
import sqlite3
import unittest

# 先经过测试夹具，确保 common 在临时目录下完成导入。
import test_tasks  # noqa: F401
from modules.task_migrations import DEFAULT_GROUPS, DEFAULT_VEHICLE_GROUPS, migrate


BASE_SCHEMA = """
CREATE TABLE members (
 id INTEGER PRIMARY KEY, name TEXT, username TEXT, password_hash TEXT,
 group_name TEXT, role TEXT, is_active INTEGER, created_at TEXT,
 session_version INTEGER DEFAULT 0
);
CREATE TABLE tasks (
 id INTEGER PRIMARY KEY, title TEXT, detail TEXT, owner_id INTEGER,
 group_name TEXT, project TEXT, priority TEXT, status TEXT, progress INTEGER,
 start_date TEXT, due_date TEXT, created_by INTEGER, created_at TEXT, updated_at TEXT
);
"""


class FailingConnection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if sql.startswith("INSERT OR IGNORE INTO schema_migrations"):
            raise sqlite3.OperationalError("injected migration failure")
        return super().execute(sql, parameters)


class TestCollaborationMigration(unittest.TestCase):
    def test_schedule_upgrade_keeps_existing_dates_and_defaults_to_no_override(self):
        db = self._legacy_db()
        db.execute("UPDATE tasks SET start_date='2026-10-14',due_date='2026-10-22'")
        db.commit()
        migrate(db)
        migrate(db)
        self.assertEqual(db.execute('SELECT start_date,due_date,schedule_stage,start_date_origin,schedule_locked FROM tasks').fetchone(),
                         ('2026-10-14', '2026-10-22', None, 'frozen', 0))
        db.close()

    def test_schedule_lock_upgrade_does_not_infer_locks_and_preserves_explicit_locks(self):
        db = self._legacy_db()
        db.execute("ALTER TABLE tasks ADD COLUMN start_date_origin TEXT NOT NULL DEFAULT 'frozen'")
        db.execute("UPDATE tasks SET start_date_origin='manual'")
        db.commit()
        migrate(db)
        self.assertEqual(db.execute('SELECT start_date_origin,schedule_locked FROM tasks').fetchone(), ('manual', 0))
        db.execute("UPDATE tasks SET start_date_origin='manual',schedule_locked=1")
        db.commit()
        migrate(db)
        self.assertEqual(db.execute('SELECT start_date_origin,schedule_locked FROM tasks').fetchone(), ('manual', 1))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute('UPDATE tasks SET schedule_locked=2')
        db.rollback()
        self.assertIsNotNone(db.execute('SELECT version FROM schema_migrations WHERE version=8').fetchone())
        db.close()

    def _legacy_db(self, factory=sqlite3.Connection):
        db = sqlite3.connect(":memory:", factory=factory)
        db.executescript(BASE_SCHEMA)
        db.execute(
            "INSERT INTO members VALUES(1,'旧成员','legacy','hash','视觉;电控','member',0,'old',0)"
        )
        db.execute(
            "INSERT INTO tasks VALUES(1,'旧任务','',1,' 视觉;电控 ',' 哨兵 ','中','未开始',0,'','',1,'old','old')"
        )
        db.commit()
        return db

    def test_migration_splits_legacy_composite_names_and_is_repeat_safe(self):
        # Given: legacy semicolon names and an inactive historical owner.
        db = self._legacy_db()

        # When: migration runs, an association is removed, and startup migration runs again.
        migrate(db)
        names = [row[0] for row in db.execute("SELECT name FROM groups ORDER BY name")]
        db.execute("DELETE FROM member_groups WHERE member_id=1")
        db.commit()
        migrate(db)

        # Then: each combined label becomes independent relations without resurrection.
        self.assertEqual(set(names), set(DEFAULT_GROUPS))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM member_groups").fetchone()[0], 0)
        vehicle_names = {row[0] for row in db.execute("SELECT name FROM projects")}
        self.assertTrue(set(DEFAULT_VEHICLE_GROUPS).issubset(vehicle_names))
        self.assertEqual(db.execute(
            "SELECT COUNT(*) FROM member_projects WHERE member_id=1"
        ).fetchone()[0], 1)
        task_names = {row[0] for row in db.execute(
            "SELECT g.name FROM task_groups tg JOIN groups g ON g.id=tg.group_id WHERE tg.task_id=1"
        )}
        self.assertEqual(task_names, {"视觉", "电控"})
        self.assertEqual(db.execute("SELECT owner_id FROM tasks WHERE id=1").fetchone()[0], 1)
        db.close()

    def test_legacy_project_memberships_are_inferred_once_without_overwriting_edits(self):
        db = self._legacy_db()
        db.execute("INSERT INTO members VALUES(2,'附属成员','second','hash','视觉','member',1,'old',0)")
        db.commit()
        migrate(db)
        project_id = db.execute("SELECT project_id FROM tasks WHERE id=1").fetchone()[0]
        self.assertEqual(
            db.execute("SELECT COUNT(*) FROM member_projects WHERE member_id=1 AND project_id=?", (project_id,)).fetchone()[0],
            1,
        )
        db.execute("DELETE FROM member_projects WHERE member_id=1")
        db.commit()
        migrate(db)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM member_projects WHERE member_id=1").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM member_projects WHERE member_id=2").fetchone()[0], 0)
        db.close()

    def test_default_labels_are_only_seeded_once(self):
        db = self._legacy_db()
        migrate(db)
        db.execute("UPDATE groups SET name='嵌入式硬件' WHERE name='硬件'")
        db.execute("DELETE FROM groups WHERE name='运营'")
        db.execute("UPDATE projects SET name='新赛季步兵' WHERE name='步兵'")
        db.execute("DELETE FROM projects WHERE name='飞镖'")
        db.commit()
        migrate(db)
        names = {row[0] for row in db.execute("SELECT name FROM groups")}
        self.assertIn("嵌入式硬件", names)
        self.assertNotIn("硬件", names)
        self.assertNotIn("运营", names)
        vehicle_names = {row[0] for row in db.execute("SELECT name FROM projects")}
        self.assertIn("新赛季步兵", vehicle_names)
        self.assertNotIn("步兵", vehicle_names)
        self.assertNotIn("飞镖", vehicle_names)
        self.assertTrue(set(range(1, 9)).issubset({row[0] for row in db.execute("SELECT version FROM schema_migrations")}))
        db.close()

    def test_injected_failure_rolls_back_entire_migration(self):
        # Given: a connection that fails only at the final version write.
        db = self._legacy_db(FailingConnection)

        # When: migration is attempted.
        with self.assertRaises(sqlite3.OperationalError):
            migrate(db)

        # Then: new tables, task columns, version, and seeded relations all rolled back.
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
        self.assertNotIn("groups", tables)
        self.assertNotIn("schema_migrations", tables)
        self.assertNotIn("version", columns)
        db.close()


if __name__ == "__main__":
    unittest.main()
