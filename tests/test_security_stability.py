"""Adversarial, synthetic-data tests for CSRF, accounting and financial exports."""
import csv
import io
import os
import sqlite3
import threading
import time
import unittest
from uuid import uuid4

from flask.testing import FlaskClient

from test_tasks import app_module
from modules import funds
from modules.funds_migrations import migrate as migrate_funds


class TestSecurityAndStability(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        reply = self.admin.post("/login", json={
            "username": "admin", "password": "ci-test-password-only",
        })
        self.assertTrue(reply.get_json()["ok"], reply.get_json())

    def _member(self, role="member"):
        username = "stability_" + uuid4().hex[:12]
        password = "synthetic-password-only"
        created = self.admin.post("/api/member", json={
            "name": "虚构成员", "username": username, "password": password, "role": role,
        })
        self.assertEqual(created.status_code, 200, created.get_json())
        return created.get_json()["id"], username, password

    def test_missing_or_cross_site_csrf_is_rejected(self):
        # Explicitly bypass only the test client's auto-header logic, not the app.
        no_token = FlaskClient.open(self.admin, "/api/task", method="POST",
                                    json={"title": "未经授权的模拟请求"})
        self.assertEqual(no_token.status_code, 403)
        self.assertEqual(no_token.get_json()["reason"], "csrf_failed")
        token = self.admin.get("/api/csrf").get_json()["csrf_token"]
        cross_site = FlaskClient.open(
            self.admin, "/api/task", method="POST",
            json={"title": "跨站模拟请求"},
            headers={"X-CSRF-Token": token, "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(cross_site.status_code, 403)
        self.assertEqual(cross_site.get_json()["reason"], "csrf_failed")

    def test_initial_password_gate_then_authenticated_rotation(self):
        _, username, password = self._member()
        member = app_module.app.test_client()
        member.auto_onboard = False
        login = member.post("/login", json={"username": username, "password": password})
        self.assertTrue(login.get_json()["must_change_password"])
        self.assertEqual(member.get("/api/data").status_code, 403)
        self.assertEqual(member.post("/api/task", json={"title": "应阻止"}).status_code, 403)
        change = member.post("/api/password", json={
            "old": password, "new": "new-synthetic-password-only",
        })
        self.assertEqual(change.status_code, 200)
        self.assertEqual(member.get("/api/data").status_code, 200)
        self.assertEqual(member.post("/login", json={
            "username": username, "password": password,
        }).status_code, 400)

    def test_money_rounded_by_record_and_exports_consistent(self):
        baseline = self.admin.get("/funds/api/stats").get_json()["data"]
        cents_before = int(baseline["total_in_cents"])
        ids = []
        for raw, expected in [("0.10", "0.10"), ("0.20", "0.20"), ("1.005", "1.01")]:
            response = self.admin.post("/funds/api/records", json={'request_id': uuid4().hex,
                "record_type": "进", "amount": raw, "record_date": "2026-10-13",
                "source_dest": "模拟金额一致性",
            })
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()["amount"], expected)
            ids.append(response.get_json()["id"])
        total = self.admin.get("/funds/api/stats").get_json()["data"]
        self.assertEqual(int(total["total_in_cents"]) - cents_before, 131)
        conn = funds.get_funds_db()
        amounts = [conn.execute("SELECT amount_cents FROM records WHERE id=?", (rid,))
                   .fetchone()[0] for rid in ids]
        conn.close()
        self.assertEqual(amounts, [10, 20, 101])
        csv_text = self.admin.get("/funds/api/export").get_data(as_text=True)
        table = list(csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"))))
        exported = {int(row["ID"]): row["金额"] for row in table if row["ID"]}
        for rid, amount in zip(ids, ("0.10", "0.20", "1.01")):
            self.assertEqual(exported[rid], amount)
        with open(funds.FUNDS_TXT, encoding="utf-8-sig") as stream:
            ledger = stream.read()
        self.assertIn("模拟金额一致性", ledger)

    def test_invalid_payloads_return_400_and_do_not_change_funds(self):
        created = self.admin.post("/funds/api/records", json={'request_id': uuid4().hex,
            "record_type": "进", "amount": "1.25", "record_date": "2026-10-13",
        }).get_json()
        rid = created["id"]
        for data in (
            {"record_type": "进", "amount": "1.00", "record_date": "2026-10-13", "notes": None},
            {"record_type": "进", "amount": "1.00", "record_date": "2026-10-13", "handler": []},
            {"record_type": "进", "amount": "1.00", "record_date": "2026-10-13", "invoice_count": 1.5},
            {"record_type": "进", "amount": "1.00", "record_date": "2026-10-13", "invoice_count": True},
        ):
            with self.subTest(payload=data):
                result = self.admin.post("/funds/api/records", json={**data, 'request_id': uuid4().hex})
                self.assertEqual(result.status_code, 400, result.get_json())
        for patch_data in ({"notes": None}, {"source_dest": ["invalid"]},
                           {"invoice_count": 3.7}, {"invoice_count": -1}):
            with self.subTest(patch=patch_data):
                result = self.admin.put(f"/funds/api/records/{rid}",
                                        json={"version": created["version"], **patch_data})
                self.assertEqual(result.status_code, 400, result.get_json())
        conn = funds.get_funds_db()
        row = conn.execute("SELECT amount_cents,invoice_count,notes FROM records WHERE id=?",
                           (rid,)).fetchone()
        conn.close()
        self.assertEqual(tuple(row), (125, 0, ""))

    def test_review_does_not_overwrite_concurrent_approval(self):
        # A competing writer commits while the HTTP request waits for its write lock.
        created = self.admin.post("/funds/api/records", json={'request_id': uuid4().hex,
            "record_type": "进", "amount": "9.90", "record_date": "2026-10-13",
        }).get_json()
        rid = created["id"]
        member_id, username, password = self._member("manager")
        grant = self.admin.post(f"/api/admin/permissions/{member_id}",
                                json={"permissions": ["funds.review"]})
        self.assertEqual(grant.status_code, 200, grant.get_json())
        reviewer = app_module.app.test_client()
        self.assertTrue(reviewer.post("/login", json={
            "username": username, "password": password,
        }).get_json()["ok"])

        writer = funds.get_funds_db()
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE records SET review_status='已审核',reviewed_by='并发审核人' "
                       "WHERE id=?", (rid,))
        outcome = []
        started = threading.Event()

        def review():
            started.set()
            outcome.append(reviewer.post(f"/funds/api/records/{rid}/review", json={"version": created["version"]}))

        thread = threading.Thread(target=review)
        thread.start()
        try:
            self.assertTrue(started.wait(2))
            time.sleep(0.2)
            writer.commit()
            thread.join(8)
            self.assertFalse(thread.is_alive(), "审核操作不得永久阻塞")
            self.assertEqual(outcome[0].status_code, 400, outcome[0].get_json())
            final = writer.execute("SELECT review_status,reviewed_by FROM records WHERE id=?",
                                   (rid,)).fetchone()
            self.assertEqual(tuple(final), ("已审核", "并发审核人"))
        finally:
            writer.rollback()
            writer.close()
            thread.join(8)

    def test_csv_exports_quote_formula_cells(self):
        injected = "=HYPERLINK(\"https://example.invalid\",\"click\")"
        created = self.admin.post("/funds/api/records", json={'request_id': uuid4().hex,
            "record_type": "进", "amount": "2.50", "record_date": "2026-10-13",
            "applicant": injected, "notes": "\t+SUM(1,2)",
        }).get_json()
        rows = list(csv.DictReader(io.StringIO(self.admin.get(
            "/funds/api/export").get_data(as_text=True).lstrip("\ufeff"))))
        row = next(row for row in rows if row["ID"] == str(created["id"]))
        self.assertEqual(row["申请人"], "'" + injected)
        self.assertEqual(row["备注"], "'\t+SUM(1,2)")

        from modules import invoices
        rel = '2026.10/未报销/csv-fixture-' + uuid4().hex + '.pdf'
        path = os.path.join(invoices.BASE_DIR, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as stream:
            stream.write(b'%PDF-1.4\n%%EOF')
        previous = invoices.load_meta()
        metadata = dict(previous)
        metadata[rel] = {'submitter': injected, 'submitter_id': 0, 'confirmed_vendor': '\t@cmd',
                         'confirmed_amount_cents': 250, 'rel_path': rel}
        invoices.save_meta(metadata)
        try:
            self.assertEqual(self.admin.post('/invoices/api/refresh').status_code, 200)
            downloaded = self.admin.get("/invoices/api/export/download")
            self.assertEqual(downloaded.status_code, 200)
            row = next(row for row in csv.DictReader(io.StringIO(downloaded.get_data(
                as_text=True).lstrip("\ufeff"))) if row['文件路径'] == rel)
            self.assertEqual(row["提交人"], "'" + injected)
            self.assertEqual(row["供应商"], "'\t@cmd")
            saved = self.admin.post("/invoices/api/export")
            self.assertEqual(saved.status_code, 200, saved.get_json())
            with open(saved.get_json()["path"], encoding="utf-8-sig", newline="") as stream:
                persisted = next(row for row in csv.DictReader(stream) if row['文件路径'] == rel)
            self.assertEqual(persisted["提交人"], "'" + injected)
        finally:
            os.remove(path)
            invoices.save_meta(previous)
            self.admin.post('/invoices/api/refresh')


class TestSessionSecretPermissions(unittest.TestCase):
    def test_generated_key_is_private_and_stable(self):
        import stat
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "secret.key")
            with patch.object(app_module, "SECRET_PATH", path):
                original = app_module._load_secret()
                self.assertEqual(len(original), 64)
                self.assertEqual(app_module._load_secret(), original)
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_existing_key_mode_is_restricted_without_rotation(self):
        import stat
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "secret.key")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write("a" * 64)
            if os.name == "posix":
                os.chmod(path, 0o644)
            with patch.object(app_module, "SECRET_PATH", path):
                self.assertEqual(app_module._load_secret(), "a" * 64)
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_empty_key_never_silently_regenerates(self):
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "secret.key")
            with open(path, "w", encoding="utf-8"):
                pass
            with patch.object(app_module, "SECRET_PATH", path):
                with self.assertRaises(RuntimeError):
                    app_module._load_secret()
            self.assertEqual(os.stat(path).st_size, 0)


class TestLegacyFundsMigrations(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("""
            CREATE TABLE records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_type TEXT, amount REAL, category TEXT, source_dest TEXT,
                applicant TEXT, invoice_count INTEGER, handler TEXT, record_date TEXT,
                notes TEXT, created_at TEXT
            )
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_legacy_amount_rounding_and_idempotent_migration(self):
        for amount in ("0.10", "0.20", "1.005"):
            self.conn.execute(
                "INSERT INTO records(record_type,amount,record_date,created_at) VALUES(?,?,?,?)",
                ("进", amount, "2026-10-13", "2026-10-13"),
            )
        self.conn.commit()
        migrate_funds(self.conn)
        migrate_funds(self.conn)
        cents = [r[0] for r in self.conn.execute(
            "SELECT amount_cents FROM records ORDER BY id")]
        self.assertEqual(cents, [10, 20, 101])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM funds_legacy_amounts").fetchone()[0], 3)

    def test_invalid_legacy_amount_rolls_back_migration(self):
        self.conn.execute(
            "INSERT INTO records(record_type,amount,record_date,created_at) VALUES(?,?,?,?)",
            ("进", "bad-amount", "2026-10-13", "2026-10-13"),
        )
        self.conn.commit()
        with self.assertRaises(RuntimeError):
            migrate_funds(self.conn)
        self.assertEqual(self.conn.execute(
            "SELECT amount FROM records").fetchone()[0], "bad-amount")
        self.assertFalse(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='funds_legacy_amounts'"
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
