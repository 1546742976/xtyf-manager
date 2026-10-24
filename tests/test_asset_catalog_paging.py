"""Isolated catalog identity, category assignment and list pagination scenarios."""
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_assets as fixture
from test_tasks import app_module
from modules.assets import migrate_assets


class CatalogPagingTest(unittest.TestCase):
    setUp = fixture.AssetsTest.setUp
    client = fixture.AssetsTest.client
    post = fixture.AssetsTest.post
    save = fixture.AssetsTest.save

    def item_detail(self, identity):
        return self.admin.get(f"/api/assets/items/{identity}").get_json()["item"]

    def intake(self, raw, **changes):
        with patch("modules.asset_scan.lookup_lcsc", return_value={"status": "unavailable", "product": None}):
            response = self.admin.post("/api/assets/scan/resolve", json={"raw": raw})
        self.assertEqual(response.status_code, 200, response.get_json())
        value = response.get_json()
        data = dict(raw=value["parsed"]["raw"], entry_id=uuid4().hex, quantity=1,
                    campus_id=self.campus, reason="合成编号核对", receipt_version=value["receipt_version"],
                    match_version=value["match_version"])
        data.update(changes)
        return self.post(self.admin, "/api/assets/scan/batch", {"receipts": [data]})["receipts"][0]

    def test_secondary_supplier_alias_is_visible_and_remains_quantity_managed(self):
        code = "C990701"
        self.post(self.admin, "/api/assets/identifiers", dict(item_id=self.screw, code=code, reason="原商品编号"))
        result = self.intake(code, draft_id="conflicting-supplier", intent="new_item", code_role="item",
                             draft=dict(name="合成重码元件", specification="独立厂商", unit="个", kind="consumable"),
                             conflict_reason="另一编号体系的同文商品，核对后另建")
        second = result["item_id"]
        self.assertNotEqual(second, self.screw)
        self.assertEqual(self.item_detail(second)["supplier_codes"], [code])
        with app_module.app.app_context():
            db = app_module.get_db()
            self.assertIsNone(db.execute("SELECT 1 FROM asset_identifiers WHERE item_id=?", (second,)).fetchone())
        detail = self.item_detail(second)
        self.post(self.admin, "/api/assets/catalog/items", dict(
            id=second, version=detail["version"], name=detail["name"], specification=detail["specification"],
            unit=detail["unit"], kind=detail["kind"], consumable_mode="status", reason="尝试切换状态管理"), 409)
        self.assertEqual(self.item_detail(second)["tracking"], "quantity")
        self.post(self.admin, "/api/assets/catalog/items", dict(
            id=self.screw, version=1, name="合成螺丝", unit="颗", kind="consumable", supplier_code=code), 409)
        self.assertEqual(self.item_detail(self.screw)["version"], 1)

    def test_catalog_supplier_code_rejects_raw_unit_alias_atomically(self):
        code = "C990702"
        result = self.intake(code, draft_id="manufacturer-serial", intent="new_item", code_role="serial",
                             draft=dict(name="合成原码电机", specification="SN", unit="台", kind="returnable", returnable_mode="unit"))
        self.assertEqual(len(result["unit_ids"]), 1)
        self.assertEqual(self.item_detail(result["item_id"])["supplier_codes"], [])
        with app_module.app.app_context():
            db = app_module.get_db()
            before = tuple(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                           for table in ("asset_items", "asset_events", "asset_identifiers"))
        for supplier_code in (code, code.lower()):
            self.post(self.admin, "/api/assets/catalog/items", dict(
                name="冲突后不应保存的档案", unit="个", kind="consumable", supplier_code=supplier_code), 409)
        with app_module.app.app_context():
            db = app_module.get_db()
            after = tuple(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                          for table in ("asset_items", "asset_events", "asset_identifiers"))
        self.assertEqual(before, after)

    def test_identity_migration_and_explicit_duplicate(self):
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("DROP INDEX asset_item_identity")
            db.execute("CREATE UNIQUE INDEX asset_item_identity ON asset_items(name,specification)")
            db.commit()
            migrate_assets(db)
            migrate_assets(db)
            index = next(r for r in db.execute("PRAGMA index_list(asset_items)") if r["name"] == "asset_item_identity")
            self.assertEqual(index["unique"], 0)
        data = dict(name="合成螺丝", specification="", unit="颗", kind="consumable")
        self.post(self.admin, "/api/assets/catalog/items", data, 409)
        self.post(self.admin, "/api/assets/catalog/items", {**data, "force_new": "yes", "reason": "另件"}, 400)
        self.post(self.admin, "/api/assets/catalog/items", {**data, "force_new": True}, 400)
        other = self.save("items", {**data, "force_new": True, "reason": "不同厂商的同型号"})
        self.assertNotEqual(other, self.screw)
        self.post(self.admin, "/api/assets/catalog/items", {**data, "id": other, "version": 1})
        distinct = self.save("items", {**data, "name": "另一档案"})
        self.post(self.admin, "/api/assets/catalog/items", {**data, "id": distinct, "version": 1}, 409)
        self.post(self.admin, "/api/assets/catalog/items", {**data, "id": distinct, "version": 1,
                  "force_new": True, "reason": "核对后修改为相同名称"})
        self.assertEqual(self.item_detail(other)["version"], 2)

    def test_category_assignment_permissions_conflict_idempotency_and_clear(self):
        category = self.save("categories", {"name": "合成驱动"})
        selections = [{"id": self.item, "version": 1}, {"id": self.screw, "version": 1}]
        data = dict(items=selections, category_id=category, category_version=1)
        self.post(self.member, "/api/assets/categories/assign", data, 403)
        self.post(self.admin, "/api/assets/categories/assign", {**data, "items": [selections[0], {"id": self.screw, "version": 99}]}, 409)
        self.assertEqual(self.item_detail(self.item)["category"], "")
        self.post(self.admin, "/api/assets/categories/assign", {**data, "category_version": 99}, 409)
        data["request_id"] = uuid4().hex
        first = self.post(self.admin, "/api/assets/categories/assign", data)
        self.assertEqual(first["changed"], [self.item, self.screw])
        self.assertEqual(first, self.post(self.admin, "/api/assets/categories/assign", data))
        self.assertEqual(self.item_detail(self.item)["version"], 2)
        categorized = self.member.get("/api/assets/items?category=合成驱动").get_json()
        self.assertEqual({i["id"] for i in categorized["items"]}, {self.item, self.screw})
        uncategorized = self.member.get("/api/assets/items?uncategorized=1").get_json()
        self.assertNotIn(self.item, [i["id"] for i in uncategorized["items"]])
        self.post(self.admin, "/api/assets/categories/assign", dict(items=[{"id": self.item, "version": 2}], category_id=None, category_version=None))
        self.assertEqual((self.item_detail(self.item)["category"], self.item_detail(self.item)["version"]), ("", 3))
        records = self.member.get(f"/api/assets/events?item_id={self.item}").get_json()["events"]
        assignment = [r for r in records if r["reason"] == "批量整理物料类别"]
        self.assertEqual(len(assignment), 2)
        self.assertEqual(assignment[0]["details"]["before"]["category"], "合成驱动")
        detail = self.member.get(f"/api/assets/items/{self.item}").get_json()
        self.assertEqual(sum(c["available"] for c in detail["campuses"]), 0)

    def test_category_assignment_rejects_invalid_batch_and_inactive_target(self):
        category = self.save("categories", {"name": "停用合成类别"})
        self.post(self.admin, "/api/assets/catalog/categories", dict(id=category, name="停用合成类别", version=1, active=False))
        data = dict(items=[{"id": self.item, "version": 1}], category_id=category, category_version=2)
        self.post(self.admin, "/api/assets/categories/assign", data, 409)
        self.post(self.admin, "/api/assets/categories/assign", {"items": data["items"]}, 400)
        for items in ([], data["items"] * 2, data["items"] * 101, [{"id": self.item, "version": True}]):
            self.post(self.admin, "/api/assets/categories/assign", dict(items=items, category_id=None), 400)
        self.assertEqual(self.item_detail(self.item)["version"], 1)

    def test_pagination_filters_clamping_and_legacy_defaults(self):
        prefix = "分页合成" + uuid4().hex
        for n in range(61):
            self.save("items", dict(name=f"{prefix}{n:03}", unit="个", kind="consumable"))
        result = self.member.get(f"/api/assets/items?q={prefix}&page=999").get_json()
        self.assertEqual((result["total"], result["page"], result["per_page"], len(result["items"])), (61, 4, 20, 1))
        result = self.member.get(f"/api/assets/items?q={prefix}&page=999&per_page=50").get_json()
        self.assertEqual((result["page"], result["per_page"], len(result["items"])), (2, 50, 11))
        result = self.member.get(f"/api/assets/items?q={prefix}&per_page=100").get_json()
        self.assertEqual(len(result["items"]), 61)
        result = self.member.get("/api/assets/items?q=不存在合成记录&page=999&per_page=50").get_json()
        self.assertEqual((result["total"], result["page"], result["items"]), (0, 1, []))
        for path, key in (("/api/assets/items", "items"), ("/api/assets/loans", "loans"),
                          ("/api/assets/events", "events"), ("/api/assets/units", "units")):
            response = self.member.get(path + "?page=999&per_page=bad").get_json()
            self.assertEqual(response["per_page"], 20)
            response = self.member.get(path + "?per_page=50&page=999").get_json()
            self.assertEqual(response["per_page"], 50)
            self.assertLessEqual(len(response[key]), 50)
            self.assertEqual(response["page"], max(1, (response["total"] + 49) // 50))

    def test_units_exact_detail_history_and_feature_flags(self):
        item = self.save("items", dict(name="分页设备型号", kind="returnable", unit="台"))
        units = self.post(self.admin, "/api/assets/units/receive", dict(item_id=item, campus_id=self.campus, quantity=25, reason="合成批次"))["unit_ids"]
        response = self.member.get(f"/api/assets/units?item_id={item}&page=99").get_json()
        self.assertEqual((response["page"], response["per_page"], len(response["units"])), (2, 20, 5))
        detail = self.member.get(f"/api/assets/units/{units[0]}").get_json()
        self.assertEqual(detail["unit"]["id"], units[0])
        self.assertEqual(detail["item"]["id"], item)
        history = self.member.get(f"/api/assets/units/{units[0]}/history?page=99").get_json()
        self.assertEqual((history["per_page"], history["page"], history["total"]), (100, 1, 1))
        history = self.member.get(f"/api/assets/units/{units[0]}/history?per_page=20").get_json()
        self.assertEqual(history["per_page"], 20)
        self.assertTrue(self.member.get("/api/assets/catalog").get_json()["intake_enabled"])
        self.assertTrue(self.member.get("/api/client/bootstrap").get_json()["asset_intake_enabled"])


if __name__ == "__main__":
    unittest.main()
