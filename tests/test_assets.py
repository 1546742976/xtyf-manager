"""Inventory API regression; every case owns a temporary copy of the test database."""
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from test_tasks import app_module
from modules.assets import migrate_assets


class AssetsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xtyf-assets-")
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "data.db")
        source = sqlite3.connect(app_module.DB_PATH)
        target = sqlite3.connect(self.path)
        source.backup(target)
        source.close()
        target.close()
        for name in ("common.DB_PATH", "app.DB_PATH", "modules.security.DB_PATH"):
            p = patch(name, self.path)
            p.start()
            self.addCleanup(p.stop)
        with app_module.app.app_context():
            db = app_module.get_db()
            self.admin_id = db.execute(
                "INSERT INTO members(name,username,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                ("资产测试管理员", uuid4().hex, "unused", "admin", "2026-10-20"),
            ).lastrowid
            self.member_id = db.execute(
                "INSERT INTO members(name,username,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                ("资产测试成员", uuid4().hex, "unused", "member", "2026-10-20"),
            ).lastrowid
            db.commit()
        self.admin, self.member = self.client(self.admin_id), self.client(self.member_id)
        self.campus = self.save("campuses", {"name": "合成东校区"})
        self.other = self.save("campuses", {"name": "合成西校区"})
        self.item = self.save("items", {"name": "合成电机", "unit": "个", "kind": "returnable"})
        self.screw = self.save("items", {"name": "合成螺丝", "unit": "颗", "kind": "consumable"})

    def client(self, uid):
        client = app_module.app.test_client()
        with client.session_transaction() as session:
            session.update(uid=uid, session_version=0)
        return client

    def post(self, client, path, data, status=200):
        response = client.post(path, json={"request_id": uuid4().hex, **data})
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def save(self, kind, data):
        return self.post(self.admin, "/api/assets/catalog/" + kind, data)["id"]

    def op(self, action, quantity=5, client=None, status=200, **extra):
        return self.post(client or self.admin, "/api/assets/operations",
                         dict(action=action, item_id=self.item, campus_id=self.campus,
                              quantity=quantity, reason="合成测试原因", **extra), status)

    def stock(self, item=None, campus=None):
        data = self.member.get(f"/api/assets/items/{item or self.item}").get_json()
        return next(c for c in data["campuses"] if c["id"] == (campus or self.campus))

    def test_borrow_partial_return_and_totals(self):
        self.op("receive", 10)
        loan = self.op("borrow", 6, self.member)["loan_id"]
        self.assertEqual((self.stock()["available"], self.stock()["borrowed"]), (4, 6))
        self.op("return", 2, self.member, loan_id=loan)
        self.assertEqual((self.stock()["available"], self.stock()["borrowed"]), (6, 4))
        self.op("return", 4, self.member, loan_id=loan)
        self.assertEqual(self.stock()["available"], 10)
        self.assertEqual(self.member.get("/api/assets/loans?mine=1").get_json()["total"], 0)

    def test_consume_and_correction_preserve_history(self):
        received = self.post(self.admin, "/api/assets/operations", dict(
            action="receive", item_id=self.screw, campus_id=self.campus, quantity=10, reason="初始库存"))
        consumed = self.post(self.member, "/api/assets/operations", dict(
            action="consume", item_id=self.screw, campus_id=self.campus, quantity=3))
        self.assertEqual(self.stock(self.screw)["consumed"], 3)
        self.op("correct", event_id=consumed["event_id"])
        self.assertEqual(self.stock(self.screw)["available"], 10)
        self.assertEqual(self.stock(self.screw)["consumed"], 0)
        self.op("correct", status=409, event_id=consumed["event_id"])
        self.op("correct", event_id=received["event_id"])
        self.assertEqual(self.stock(self.screw)["available"], 0)

    def test_transfer_atomic_and_visible_in_both_campuses(self):
        self.op("receive", 10)
        result = self.op("transfer", 4, target_campus_id=self.other)
        self.assertEqual(self.stock()["available"], 6)
        self.assertEqual(self.stock(campus=self.other)["available"], 4)
        records = self.admin.get(f"/api/assets/events?campus_id={self.other}").get_json()["events"]
        self.assertIn(result["event_id"], [r["id"] for r in records])
        self.op("transfer", 7, status=409, target_campus_id=self.other)
        self.assertEqual(self.stock()["available"], 6)
        self.op("correct", event_id=result["event_id"])
        self.assertEqual(self.stock(campus=self.other)["available"], 0)

    def test_adjust_checks_version(self):
        self.op("receive", 10)
        version = self.stock()["stock_version"]
        self.op("borrow", 1, self.member)
        self.op("adjust", 3, status=409, version=version)
        self.op("adjust", 3, version=self.stock()["stock_version"])
        self.assertEqual((self.stock()["available"], self.stock()["borrowed"]), (3, 1))

    def test_idempotency_and_payload_conflict(self):
        payload = dict(request_id=uuid4().hex, action="receive", item_id=self.item,
                       campus_id=self.campus, quantity=10, reason="测试")
        first = self.post(self.admin, "/api/assets/operations", payload)
        self.assertEqual(first, self.post(self.admin, "/api/assets/operations", payload))
        self.post(self.admin, "/api/assets/operations", {**payload, "quantity": 11}, 409)
        self.assertEqual(self.stock()["available"], 10)

    def test_concurrent_borrow_cannot_overdraw(self):
        self.op("receive", 5)
        clients = [self.client(self.member_id), self.client(self.member_id)]
        def borrow(client):
            return client.post("/api/assets/operations", json=dict(
                request_id=uuid4().hex, action="borrow", item_id=self.item,
                campus_id=self.campus, quantity=4)).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(borrow, clients)), [200, 409])
        self.assertEqual(self.stock()["available"], 1)

    def test_own_loans_only_and_return_limit(self):
        self.op("receive")
        loan = self.op("borrow", 3, self.member)["loan_id"]
        self.op("return", 1, status=403, loan_id=loan)
        self.op("return", 4, self.member, status=409, loan_id=loan)
        self.assertEqual(self.stock()["borrowed"], 3)

    def test_permissions_and_delegation(self):
        self.op("receive", client=self.member, status=403)
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("INSERT INTO member_permissions VALUES(?,?,?,?)",
                       (self.member_id, "assets.manage", self.admin_id, "2026-10-20"))
            db.commit()
        self.op("receive", client=self.member)
        self.assertTrue(self.member.get("/api/assets/catalog").get_json()["can_manage"])

    def test_validation_and_type(self):
        for quantity in (0, -1, 1.5, True, "1", 1000000001):
            self.op("borrow", quantity, self.member, status=400)
        self.op("consume", 1, self.member, status=400)
        self.op("borrow", 1, self.member, status=409)
        self.op("unknown", 1, status=400)
        self.post(self.admin, "/api/assets/operations",
                  dict(action="receive", item_id=self.item, campus_id=self.campus, quantity=1), 400)

    def test_catalog_versions_units_and_deactivation(self):
        self.op("receive")
        data = dict(id=self.item, name="合成电机", unit="个", kind="returnable", version=1)
        self.post(self.admin, "/api/assets/catalog/items", {**data, "active": False}, 409)
        self.post(self.admin, "/api/assets/catalog/items", {**data, "unit": "台"}, 409)
        self.post(self.admin, "/api/assets/catalog/items", {**data, "name": "新名称"})
        self.post(self.admin, "/api/assets/catalog/items", data, 409)
        self.post(self.admin, "/api/assets/catalog/campuses",
                  dict(id=self.campus, name="合成东校区", active=False, version=1), 409)

    def test_correction_refuses_after_downstream_use(self):
        receipt = self.op("receive", 5)
        borrowed = self.op("borrow", 4, self.member)
        self.op("correct", status=409, event_id=receipt["event_id"])
        self.op("return", 2, self.member, loan_id=borrowed["loan_id"])
        self.op("correct", status=409, event_id=borrowed["event_id"])
        self.assertEqual(self.stock()["borrowed"], 2)

    def test_failure_rolls_back_every_table(self):
        self.op("receive", 5)
        with patch("modules.assets.event", side_effect=sqlite3.OperationalError("disk failure")):
            self.op("borrow", 3, self.member, status=503)
        self.assertEqual(self.stock()["available"], 5)
        self.assertEqual(self.stock()["borrowed"], 0)

    def test_history_immutable_and_backup_restores(self):
        self.op("receive", 8)
        connection = sqlite3.connect(self.path)
        self.addCleanup(connection.close)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM asset_events")
        connection.rollback()
        backup = sqlite3.connect(":memory:")
        self.addCleanup(backup.close)
        connection.backup(backup)
        migrate_assets(backup)
        migrate_assets(backup)
        self.assertEqual(backup.execute("SELECT available FROM asset_stock WHERE item_id=?", (self.item,)).fetchone()[0], 8)
        self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_auth_csrf_first_password_and_disabled_member(self):
        from flask.testing import FlaskClient
        raw = FlaskClient(app_module.app)
        self.assertEqual(raw.get("/api/assets/catalog").status_code, 401)
        with raw.session_transaction() as session:
            session.update(uid=self.member_id, session_version=0)
        self.assertEqual(raw.post("/api/assets/operations", json={}).status_code, 403)
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("UPDATE members SET must_change_password=1 WHERE id=?", (self.member_id,))
            db.commit()
        self.assertEqual(self.member.get("/api/assets/catalog").status_code, 403)
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("UPDATE members SET is_active=0 WHERE id=?", (self.member_id,))
            db.commit()
        self.assertEqual(self.member.get("/api/assets/catalog").status_code, 401)

    def test_search_paging_snapshot_names_and_query_count(self):
        self.op("receive", 5)
        self.op("borrow", 2, self.member)
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("UPDATE members SET name='新姓名' WHERE id=?", (self.member_id,))
            db.commit()
        records = self.member.get(f"/api/assets/events?item_id={self.item}").get_json()["events"]
        self.assertEqual(next(r for r in records if r["action"] == "borrow")["member_name"], "资产测试成员")
        response = self.member.get("/api/assets/items?q=合成电机").get_json()
        self.assertEqual(response["total"], 1)
        self.assertEqual(response["items"][0]["total"], 5)
        for i in range(22):
            self.save("items", dict(name=f"分页合成{i}", unit="个", kind="returnable"))
        self.assertEqual(len(self.member.get("/api/assets/items?q=分页合成").get_json()["items"]), 20)
        self.assertEqual(len(self.member.get("/api/assets/items?q=分页合成&page=2").get_json()["items"]), 2)
        from modules.assets import totals
        with app_module.app.app_context():
            db = app_module.get_db()
            queries = []
            db.set_trace_callback(queries.append)
            totals(db)
            self.assertEqual(len(queries), 3)
        # Snapshots are strings, not links to mutable member display data.
        json.dumps(records, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
