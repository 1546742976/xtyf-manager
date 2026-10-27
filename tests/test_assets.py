"""Inventory API regression; every case owns a temporary copy of the test database."""
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
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
        # This suite exercises pre-migration quantity inventory and PR #45 safeguards.
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("UPDATE asset_items SET tracking='legacy' WHERE id=?", (self.item,))
            db.commit()

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

    def test_inventory_views_are_server_filtered_before_pagination(self):
        supply = self.save("items", {"name": "合成自由取用耗材", "unit": "盒",
                                     "kind": "consumable", "consumable_mode": "status"})
        base = "/api/assets/items?q=合成"
        all_items = self.member.get(base).get_json()
        self.assertTrue(all_items["ok"])
        summary = all_items["summary"]
        self.assertGreaterEqual(summary["equipment"], 1)
        self.assertGreaterEqual(summary["quantity"], 1)
        self.assertGreaterEqual(summary["supply"], 1)
        self.assertEqual(summary["all"], sum(summary[key] for key in ("equipment", "quantity", "supply")))
        self.assertGreaterEqual(summary["attention"], 1)
        for view, expected in (("equipment", self.item), ("quantity", self.screw), ("supply", supply)):
            result = self.member.get(base + "&view=" + view).get_json()
            self.assertEqual(result["total"], summary[view])
            self.assertIn(expected, [i["id"] for i in result["items"]])
            self.assertEqual(result["summary"], summary)
        attention = self.member.get(base + "&view=attention").get_json()
        self.assertEqual(attention["total"], summary["attention"])
        self.assertIn(supply, [i["id"] for i in attention["items"]])
        self.assertIn(self.screw, [i["id"] for i in attention["items"]])
        self.assertEqual(self.member.get(base + "&view=all").get_json()["total"], summary["all"])

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
            action="consume", item_id=self.screw, campus_id=self.campus, quantity=3, reason="合成装配领用"))
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
        self.assertEqual(first['request_id'], payload['request_id'])
        self.assertTrue(self.admin.get('/api/assets/catalog').get_json()['write_receipts'])
        self.assertEqual(first, self.post(self.admin, "/api/assets/operations", payload))
        self.post(self.admin, "/api/assets/operations", {**payload, "quantity": 11}, 409)
        self.assertEqual(self.stock()["available"], 10)

    def test_concurrent_borrow_cannot_overdraw(self):
        self.op("receive", 5)
        clients = [self.client(self.member_id), self.client(self.member_id)]
        def borrow(client):
            return client.post("/api/assets/operations", json=dict(
                request_id=uuid4().hex, action="borrow", item_id=self.item,
                campus_id=self.campus, quantity=4, reason="合成并发借用")).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(borrow, clients)), [200, 409])
        self.assertEqual(self.stock()["available"], 1)

    def test_aggregate_reads_one_snapshot_during_concurrent_borrow(self):
        from modules.assets import totals
        self.op("receive", 10)
        with app_module.app.app_context():
            reader = app_module.get_db()
            changed = []
            def commit_between_reads(sql):
                if "SUM(outstanding)" not in sql or changed:
                    return
                changed.append(True)
                with closing(sqlite3.connect(self.path)) as writer, writer:
                    writer.execute("UPDATE asset_stock SET available=available-1 WHERE item_id=? AND campus_id=?",
                                   (self.item, self.campus))
                    writer.execute(
                        "INSERT INTO asset_loans(item_id,campus_id,member_id,member_name,quantity,outstanding,created_at)"
                        " VALUES(?,?,?,'并发测试',1,1,'2026-10-20')", (self.item, self.campus, self.member_id))
            reader.set_trace_callback(commit_between_reads)
            before = totals(reader)[self.item, self.campus]
            reader.set_trace_callback(None)
            after = totals(reader)[self.item, self.campus]
            self.assertTrue(changed)
            self.assertEqual((before["available"], before.get("borrowed", 0)), (10, 0))
            self.assertEqual((after["available"], after["borrowed"]), (9, 1))

    def test_own_loans_only_and_return_limit(self):
        self.op("receive")
        loan = self.op("borrow", 3, self.member)["loan_id"]
        self.op("return", 1, status=403, loan_id=loan)
        self.op("return", 4, self.member, status=409, loan_id=loan)
        self.assertEqual(self.stock()["borrowed"], 3)

    def test_permissions_and_delegation(self):
        self.assertEqual(self.member.get("/assets").status_code, 200)
        self.assertEqual(self.member.get(f"/assets/detail?id={self.item}").status_code, 200)
        self.op("receive", client=self.member)
        self.post(self.member, "/api/assets/catalog/items",
                  dict(name="不可创建", unit="个", kind="returnable"), 403)
        self.op("transfer", client=self.member, target_campus_id=self.other, status=403)
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("INSERT INTO member_permissions VALUES(?,?,?,?)",
                       (self.member_id, "assets.manage", self.admin_id, "2026-10-20"))
            db.commit()
        self.op("receive", client=self.member)
        self.assertTrue(self.member.get("/api/assets/catalog").get_json()["can_manage"])
        bootstrap = self.member.get("/api/client/bootstrap").get_json()
        self.assertTrue(bootstrap["assets_enabled"])
        self.assertTrue(bootstrap["permissions"]["manage_assets"])

    def test_manager_catalog_and_all_members_quantity_changes(self):
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("UPDATE members SET role='manager' WHERE id=?", (self.admin_id,))
            db.commit()
        category = self.save("categories", {"name": "动力部件"})
        item = self.save("items", dict(name="项管物品", unit="个", kind="returnable", category="动力部件"))
        self.assertTrue(self.admin.get("/api/client/bootstrap").get_json()["permissions"]["manage_assets"])
        self.assertFalse(self.member.get("/api/assets/catalog").get_json()["can_manage"])
        self.op("receive", 8, self.member)
        self.op("adjust", 4, self.member, version=self.stock()["stock_version"])
        self.assertEqual(self.stock()["available"], 4)
        self.op("adjust", 9, self.member, version=self.stock()["stock_version"])
        self.assertEqual(self.stock()["available"], 9)
        self.op("adjust", 10, self.member, version=1, status=409)
        self.post(self.member, "/api/assets/operations", dict(
            action="adjust", item_id=self.item, campus_id=self.campus, quantity=0,
            version=self.stock()["stock_version"]), 400)
        self.post(self.member, "/api/assets/catalog/categories", dict(name="不可创建"), 403)
        self.post(self.admin, "/api/assets/catalog/categories",
                  dict(id=category, name="动力", version=1))
        edited = self.admin.get(f"/api/assets/items/{item}").get_json()["item"]
        self.assertEqual((edited["category"], edited["version"]), ("动力", 2))
        self.post(self.admin, "/api/assets/catalog/categories",
                  dict(id=category, name="动力", version=2, active=False), 409)
        self.post(self.admin, "/api/assets/catalog/items",
                  dict(id=item, name="项管物品", unit="个", kind="returnable",
                       category="动力", version=2, active=False))
        self.post(self.admin, "/api/assets/catalog/categories",
                  dict(id=category, name="动力", version=2, active=False))
        self.post(self.admin, "/api/assets/catalog/items",
                  dict(name="停用类别不可新选", unit="个", kind="returnable", category="动力"), 409)

    def test_registration_time_and_immutable_submission_time(self):
        self.op("receive", 10, self.member)
        with patch("modules.assets.stamp", return_value="2026-10-20 08:00:00"):
            for action, item in (("borrow", self.item), ("consume", self.screw)):
                if item != self.item:
                    self.post(self.member, "/api/assets/operations", dict(
                        action="receive", item_id=item, campus_id=self.campus, quantity=10, reason="合成入库"))
                result = self.post(self.member, "/api/assets/operations", dict(
                    action=action, item_id=item, campus_id=self.campus, quantity=2,
                    occurred_at="2026-10-19T23:30", reason="合成补登记"))
                record = next(e for e in self.member.get(f"/api/assets/events?item_id={item}").get_json()["events"]
                              if e["id"] == result["event_id"])
                self.assertEqual(record["created_at"], "2026-10-20 08:00:00")
                self.assertEqual(record["details"]["occurred_at"], "2026-10-19 23:30:00")
            for value in ("2026-10-20T08:01", "2026-02-30T10:00", "", 7, "2026-10-20T08:00Z"):
                self.op("borrow", 1, self.member, occurred_at=value, status=400)
        self.assertEqual(self.stock()["borrowed"], 2)

    def test_category_migration_preserves_existing_names_and_is_repeatable(self):
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("UPDATE asset_items SET category='旧类别' WHERE id=?", (self.item,))
            db.commit()
            migrate_assets(db)
            migrate_assets(db)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM asset_categories WHERE name='旧类别'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT category FROM asset_items WHERE id=?", (self.item,)).fetchone()[0], "旧类别")

    def test_registration_requires_nonblank_reason_without_changing_stock(self):
        self.op("receive", 10)
        loan = self.op("borrow", 2, self.member)["loan_id"]
        self.post(self.admin, "/api/assets/operations", dict(
            action="receive", item_id=self.screw, campus_id=self.campus, quantity=10, reason="合成入库"))
        for action, item in (("borrow", self.item), ("consume", self.screw), ("return", self.item)):
            for fields in ({}, {"reason": ""}, {"reason": " \t\n "}, {"reason": None}):
                self.post(self.member, "/api/assets/operations", dict(
                    action=action, item_id=item, campus_id=self.campus, quantity=1,
                    loan_id=loan, **fields), 400)
        self.assertEqual((self.stock()["available"], self.stock()["borrowed"]), (8, 2))
        self.assertEqual(self.stock(self.screw)["available"], 10)
        self.assertEqual(self.stock(self.screw)["consumed"], 0)

    def test_asset_login_links_are_local_and_bounded(self):
        for target in ("/assets", "/assets?view=mine", "/assets/detail?id=12"):
            self.assertEqual(app_module.task_login_destination(target), target)
        for target in ("//evil.test/assets", "/assets/detail?id=-1", "/assets?next=https://evil.test"):
            self.assertEqual(app_module.task_login_destination(target), "/")

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


    def test_correction_refuses_after_newer_absolute_stock_count(self):
        receipt = self.op("receive", 10)
        first_count = self.op("adjust", 5, version=self.stock()["stock_version"])
        self.op("adjust", 7, version=self.stock()["stock_version"])
        self.op("correct", event_id=first_count["event_id"], status=409)
        self.op("correct", event_id=receipt["event_id"], status=409)
        self.assertEqual(self.stock()["available"], 7)
        records = self.admin.get(f"/api/assets/events?item_id={self.item}").get_json()["events"]
        self.assertFalse(any(e["action"] == "correct" for e in records))

    def test_transfer_correction_refuses_after_target_campus_count(self):
        self.op("receive", 10)
        moved = self.op("transfer", 4, target_campus_id=self.other)
        self.post(self.admin, "/api/assets/operations", {
            "action": "adjust", "item_id": self.item, "campus_id": self.other,
            "quantity": 3, "version": self.stock(campus=self.other)["stock_version"],
            "reason": "合成目标校区盘点"})
        self.op("correct", event_id=moved["event_id"], status=409)
        self.assertEqual(self.stock()["available"], 6)
        self.assertEqual(self.stock(campus=self.other)["available"], 3)

    def test_correction_rejects_inactive_campus_and_item(self):
        self.op("receive", 5)
        counted = self.op("adjust", 0, version=self.stock()["stock_version"])
        self.post(self.admin, "/api/assets/catalog/campuses", {
            "id": self.campus, "name": "合成东校区", "version": 1, "active": False})
        self.op("correct", event_id=counted["event_id"], status=409)
        self.assertEqual(self.stock()["available"], 0)
        self.post(self.admin, "/api/assets/catalog/campuses", {
            "id": self.campus, "name": "合成东校区", "version": 2, "active": True})
        self.post(self.admin, "/api/assets/catalog/items", {
            "id": self.item, "name": "合成电机", "unit": "个",
            "kind": "returnable", "version": 1, "active": False})
        self.op("correct", event_id=counted["event_id"], status=409)
        self.assertEqual(self.stock()["available"], 0)

    def test_assets_reject_large_payload_and_unsupported_fields(self):
        self.post(self.member, "/api/assets/operations", {
            "action": "receive", "item_id": self.item, "campus_id": self.campus,
            "quantity": 1, "reason": "合成测试", "debug": "extra"}, 400)
        self.post(self.member, "/api/assets/operations", {
            "action": "receive", "item_id": self.item, "campus_id": self.campus,
            "quantity": 1, "reason": "合成测试", "padding": "x" * 20000}, 413)
        self.post(self.admin, "/api/assets/catalog/items", {
            "name": "恶意冗余字段", "unit": "个", "kind": "returnable",
            "padding": "x" * 20000}, 413)
        self.assertEqual(self.stock()["available"], 0)
        self.assertFalse(any(r["name"] == "恶意冗余字段" for r in
                             self.admin.get("/api/assets/items").get_json()["items"]))

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

    def test_additive_migration_preserves_existing_rows_and_starts_empty(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE members(id INTEGER PRIMARY KEY,name TEXT)")
            db.execute("INSERT INTO members VALUES(5,'原成员')")
            db.execute("CREATE TABLE existing_business(id INTEGER PRIMARY KEY,body TEXT)")
            db.execute("INSERT INTO existing_business VALUES(7,'原有业务')")
            migrate_assets(db)
            migrate_assets(db)
            self.assertEqual(db.execute("SELECT name FROM members WHERE id=5").fetchone()[0], "原成员")
            self.assertEqual(db.execute("SELECT body FROM existing_business WHERE id=7").fetchone()[0], "原有业务")
            for table in ("asset_items", "asset_campuses", "asset_stock", "asset_loans", "asset_events"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

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
            self.assertEqual(len([q for q in queries if q.startswith("SELECT")]), 4)
        # Snapshots are strings, not links to mutable member display data.
        json.dumps(records, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
