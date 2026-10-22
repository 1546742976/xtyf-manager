"""Durable invoice-job crash recovery and cross-process lock tests (synthetic SQLite)."""
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

# 先经过测试夹具，确保项目模块导入时已经指向临时目录。
import test_tasks  # noqa: F401
from modules import invoice_jobs
from modules.invoice_storage import InvoiceFileLock


class TestInvoiceJobRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xtyf-invoice-jobs-test-")
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "fixture-data.db"
        self.lock_path = Path(self.temp.name) / ".invoice.lock"
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.executescript("""
                CREATE TABLE members (
                    id INTEGER PRIMARY KEY,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    deleted_at TEXT,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    session_version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE member_permissions (
                    member_id INTEGER NOT NULL, permission TEXT NOT NULL,
                    PRIMARY KEY(member_id, permission)
                );
                CREATE TABLE security_audit (
                    id INTEGER PRIMARY KEY,
                    details TEXT NOT NULL DEFAULT '{}',
                    outcome TEXT DEFAULT 'pending',
                    finished_at TEXT
                );
                INSERT INTO members(id) VALUES(1);
                INSERT INTO member_permissions VALUES(1, 'invoices.manage');
            """)
            invoice_jobs.migrate(db)
        self.patch_db = patch.object(invoice_jobs, "DB_PATH", str(self.db_path))
        self.patch_db.start()
        self.addCleanup(self.patch_db.stop)

    def _insert_job(self, kind="refresh", *, attempts=0, status="queued"):
        jid = uuid.uuid4().hex
        with closing(sqlite3.connect(self.db_path)) as db, db:
            audit_id = db.execute(
                "INSERT INTO security_audit(details) VALUES('{}')"
            ).lastrowid
            db.execute(
                "INSERT INTO invoice_jobs(id,type,actor_id,actor_name,session_version,"
                "audit_id,status,progress,message,attempts,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (jid, kind, 1, "虚构财务", 0, audit_id, status, 0,
                 "模拟任务", attempts, time.time()),
            )
        return jid, audit_id

    def _db_row(self, jid):
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.row_factory = sqlite3.Row
            return dict(db.execute("SELECT * FROM invoice_jobs WHERE id=?", (jid,)).fetchone())

    def _audit(self, audit_id):
        with closing(sqlite3.connect(self.db_path)) as db, db:
            return db.execute("SELECT outcome FROM security_audit WHERE id=?",
                              (audit_id,)).fetchone()[0]

    def test_readonly_job_crash_retries_and_finishes_with_audit(self):
        jid, audit_id = self._insert_job("refresh")
        lock = InvoiceFileLock(self.lock_path)
        with lock:
            claimed = invoice_jobs.recover_and_claim()
            self.assertEqual(claimed["id"], jid)
            self.assertEqual(self._db_row(jid)["attempts"], 1)
            # Simulated process termination: persisted running row, no finish_job.
        with lock:
            replay = invoice_jobs.recover_and_claim()
            self.assertEqual(replay["id"], jid)
            self.assertEqual(self._db_row(jid)["attempts"], 2)
            invoice_jobs.finish_job(jid, result={"processed": 3})
        result = self._db_row(jid)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(self._audit(audit_id), "success")
        with lock:
            self.assertIsNone(invoice_jobs.recover_and_claim())

    def test_destructive_job_crash_requires_manual_reconciliation(self):
        for kind in ("organize", "delete_dups"):
            with self.subTest(type=kind):
                jid, audit_id = self._insert_job(kind)
                lock = InvoiceFileLock(self.lock_path)
                with lock:
                    self.assertEqual(invoice_jobs.recover_and_claim()["id"], jid)
                with lock:
                    self.assertIsNone(invoice_jobs.recover_and_claim())
                self.assertEqual(self._db_row(jid)["status"], "interrupted")
                self.assertEqual(self._db_row(jid)["attempts"], 1)
                self.assertEqual(self._audit(audit_id), "failure")

    def test_three_failed_attempts_are_not_retried(self):
        jid, audit_id = self._insert_job("scan_dups", attempts=3, status="running")
        with InvoiceFileLock(self.lock_path):
            self.assertIsNone(invoice_jobs.recover_and_claim())
        self.assertEqual(self._db_row(jid)["status"], "interrupted")
        self.assertEqual(self._audit(audit_id), "failure")

    def test_revoke_permission_before_claim_blocks_execution(self):
        jid, audit_id = self._insert_job("refresh")
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("DELETE FROM member_permissions WHERE member_id=1")
        with InvoiceFileLock(self.lock_path):
            self.assertIsNone(invoice_jobs.recover_and_claim())
        self.assertEqual(self._db_row(jid)["status"], "error")
        self.assertEqual(self._db_row(jid)["attempts"], 0)
        self.assertEqual(self._audit(audit_id), "failure")

    def test_stream_actor_validation_rejects_invalid_account_or_session(self):
        self.assertTrue(invoice_jobs.actor_is_current(1, 0))
        self.assertFalse(invoice_jobs.actor_is_current(1, 1))
        for column, value in (("is_active", 0), ("deleted_at", "2026-10-22"),
                              ("must_change_password", 1)):
            with self.subTest(column=column):
                with closing(sqlite3.connect(self.db_path)) as db, db:
                    db.execute(f"UPDATE members SET {column}=? WHERE id=1", (value,))
                self.assertFalse(invoice_jobs.actor_is_current(1, 0))
                with closing(sqlite3.connect(self.db_path)) as db, db:
                    db.execute("UPDATE members SET is_active=1,deleted_at=NULL,must_change_password=0 WHERE id=1")
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("DELETE FROM member_permissions WHERE member_id=1")
        self.assertFalse(invoice_jobs.actor_is_current(1, 0))

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires POSIX flock subprocess")
    def test_two_processes_claim_one_job_only_once(self):
        jid, audit_id = self._insert_job("scan_dups")
        source = """
import os, time
from modules.invoice_storage import InvoiceFileLock
from modules import invoice_jobs
with InvoiceFileLock(os.environ['INVOICE_TEST_LOCK']):
    job = invoice_jobs.recover_and_claim()
    if job:
        time.sleep(0.15)
        invoice_jobs.finish_job(job['id'], result={'ok': True})
        print(job['id'])
    else:
        print('NONE')
"""
        env = dict(os.environ)
        env["PROGRESS_DB"] = str(self.db_path)
        env["INVOICE_TEST_LOCK"] = str(self.lock_path)
        cwd = str(Path(__file__).resolve().parents[1])
        procs = [
            subprocess.Popen([sys.executable, "-c", source], env=env, cwd=cwd,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        try:
            outputs = []
            for proc in procs:
                stdout, stderr = proc.communicate(timeout=20)
                self.assertEqual(proc.returncode, 0, stderr)
                outputs.append(stdout.strip())
            self.assertEqual(sorted(outputs), sorted([jid, "NONE"]))
            self.assertEqual(self._db_row(jid)["attempts"], 1)
            self.assertEqual(self._db_row(jid)["status"], "done")
            self.assertEqual(self._audit(audit_id), "success")
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()


if __name__ == "__main__":
    unittest.main()
