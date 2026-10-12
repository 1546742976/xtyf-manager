"""Transactional, repeat-safe collaboration migration tests."""
import sqlite3
import unittest

from modules.task_migrations import migrate


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

    def test_migration_preserves_exact_legacy_names_and_is_repeat_safe(self):
        # Given: legacy semicolon names and an inactive historical owner.
        db = self._legacy_db()

        # When: migration runs, an association is removed, and startup migration runs again.
        migrate(db)
        names = [row[0] for row in db.execute("SELECT name FROM groups ORDER BY name")]
        db.execute("DELETE FROM member_groups WHERE member_id=1")
        db.commit()
        migrate(db)

        # Then: names are trimmed but not split and removed history is not resurrected.
        self.assertEqual(names, ["视觉;电控"])
        self.assertEqual(db.execute("SELECT COUNT(*) FROM member_groups").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT owner_id FROM tasks WHERE id=1").fetchone()[0], 1)
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
