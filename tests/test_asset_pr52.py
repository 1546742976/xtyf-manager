"""PR #52 regression tests for server-side filters and returnable tracking."""
import unittest
from unittest.mock import patch

import test_assets as fixture


class AssetsPR52Test(unittest.TestCase):
    setUp = fixture.AssetsTest.setUp
    client = fixture.AssetsTest.client
    post = fixture.AssetsTest.post
    save = fixture.AssetsTest.save

    def item_record(self, identity):
        response = self.member.get(f"/api/assets/items/{identity}")
        self.assertEqual(response.status_code, 200)
        return response.get_json()["item"]

    def edit(self, identity, status=200, **fields):
        before = self.item_record(identity)
        return self.post(self.admin, "/api/assets/catalog/items", {
            "id": identity, "version": before["version"],
            "name": before["name"], "specification": before["specification"],
            "category": before["category"], "unit": before["unit"],
            "kind": before["kind"], "reason": "合成管理方式核对", **fields,
        }, status)

    def test_filtered_counts_use_full_result_not_current_page(self):
        for index in range(22):
            self.save("items", {
                "name": f"PR52分页设备{index:02d}", "unit": "台",
                "kind": "returnable", "category": "PR52设备分类",
            })
        self.save("items", {
            "name": "PR52分页耗材", "unit": "颗",
            "kind": "consumable", "category": "PR52其它分类",
        })
        base = "/api/assets/items?q=PR52分页"
        first = self.member.get(base + "&view=equipment").get_json()
        second = self.member.get(base + "&view=equipment&page=2").get_json()
        self.assertEqual(first["summary"]["all"], 23)
        self.assertEqual(first["summary"]["equipment"], 22)
        self.assertEqual(first["summary"]["quantity"], 1)
        self.assertEqual(first["total"], second["total"])
        self.assertEqual(len(first["items"]), 20)
        self.assertEqual(len(second["items"]), 2)
        self.assertEqual(first["summary"], second["summary"])
        self.assertEqual(self.member.get(base + "&view=quantity").get_json()["total"], 1)
        only_category = self.member.get(base + "&category=PR52设备分类&view=equipment").get_json()
        self.assertEqual(only_category["summary"]["all"], 22)
        self.assertEqual(only_category["summary"]["quantity"], 0)
        self.assertEqual(only_category["total"], 22)
        self.assertEqual(self.member.get("/api/assets/items?q=PR52不存在").get_json()["total"], 0)

    def test_attention_excludes_inactive_and_in_stock_consumables(self):
        missing = self.save("items", {"name": "PR52补货缺货", "kind": "consumable", "unit": "个"})
        stocked = self.save("items", {"name": "PR52补货有货", "kind": "consumable", "unit": "个"})
        low = self.save("items", {"name": "PR52补货状态不足", "kind": "consumable",
                                  "unit": "盒", "consumable_mode": "status"})
        inactive = self.save("items", {"name": "PR52补货已停用", "kind": "consumable",
                                       "unit": "盒", "consumable_mode": "status"})
        self.post(self.admin, "/api/assets/operations", {
            "action": "receive", "item_id": stocked, "campus_id": self.campus,
            "quantity": 3, "reason": "合成充足库存",
        })
        self.edit(inactive, active=False)
        result = self.member.get("/api/assets/items?q=PR52补货&view=attention").get_json()
        self.assertEqual({item["id"] for item in result["items"]}, {missing, low})
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["summary"]["all"], 4)
        self.assertEqual(result["summary"]["attention"], 2)
        self.assertEqual(result["summary"]["quantity"], 2)
        self.assertEqual(result["summary"]["supply"], 2)

    def test_quantity_returnable_migration_cancel_restores_original_mode(self):
        identity = self.save("items", {
            "name": "PR52按数量借还工具", "kind": "returnable",
            "unit": "台", "returnable_mode": "quantity",
        })
        self.assertEqual(self.item_record(identity)["tracking"], "quantity")
        self.post(self.admin, "/api/assets/operations", {
            "action": "receive", "item_id": identity, "campus_id": self.campus,
            "quantity": 3, "reason": "合成入库",
        })
        loan = self.post(self.member, "/api/assets/operations", {
            "action": "borrow", "item_id": identity, "campus_id": self.campus,
            "quantity": 1, "reason": "合成借用",
        })["loan_id"]
        self.post(self.member, "/api/assets/operations", {
            "action": "return", "item_id": identity, "campus_id": self.campus,
            "loan_id": loan, "quantity": 1, "reason": "合成归还",
        })
        self.edit(identity, status=409, returnable_mode="unit")

        def migrate(action, status=200, **extra):
            return self.post(self.admin, "/api/assets/units/migrate", {
                "item_id": identity, "version": self.item_record(identity)["version"],
                "action": action, "reason": "合成逐件核对", **extra,
            }, status)

        migrate("start")
        self.assertEqual(self.item_record(identity)["tracking"], "migrating")
        self.edit(identity, status=409, returnable_mode="quantity")
        migrate("cancel")
        self.assertEqual(self.item_record(identity)["tracking"], "quantity")
        migrate("start")
        migrate("add", campus_id=self.campus, quantity=3)
        finished = migrate("finish")
        self.assertEqual(len(finished["unit_ids"]), 3)
        self.assertEqual(self.item_record(identity)["tracking"], "unit")
        self.assertEqual(self.member.get(f"/api/assets/units?item_id={identity}").get_json()["total"], 3)
        self.edit(identity, status=409, returnable_mode="quantity")
        migrate("start", status=409)

    def test_tracking_mode_validation_and_single_identity_guard(self):
        self.post(self.admin, "/api/assets/catalog/items", {
            "name": "PR52非法管理方式", "kind": "returnable", "unit": "台",
            "returnable_mode": "status",
        }, 400)
        self.post(self.admin, "/api/assets/catalog/items", {
            "name": "PR52耗材错误模式", "kind": "consumable", "unit": "盒",
            "returnable_mode": "quantity",
        }, 400)
        identity = self.save("items", {"name": "PR52默认单件设备", "kind": "returnable", "unit": "台"})
        self.assertEqual(self.item_record(identity)["tracking"], "unit")
        self.edit(identity, status=409, returnable_mode="quantity")
        self.assertEqual(self.item_record(identity)["tracking"], "unit")

    def test_package_create_receive_keeps_returnable_quantity_tracking(self):
        raw = "{on:PR52-ORDER,pc:C87654,pm:PR52-TOOL,qty:2}"
        body = {
            "raw": raw, "name": "PR52包装工具", "specification": "PR52-TOOL",
            "kind": "returnable", "returnable_mode": "quantity", "unit": "个",
            "campus_id": self.campus, "quantity": 2, "receipt_version": 0,
            "reason": "合成包装实收",
        }
        self.post(self.member, "/api/assets/scan/create-receive", body, 403)
        response = self.post(self.admin, "/api/assets/scan/create-receive", body)
        identity = response["item_id"]
        self.assertEqual(self.item_record(identity)["tracking"], "quantity")
        stock = self.member.get(f"/api/assets/items/{identity}").get_json()["campuses"]
        self.assertEqual(next(row["available"] for row in stock if row["id"] == self.campus), 2)
        self.assertEqual(self.member.get(f"/api/assets/units?item_id={identity}").get_json()["total"], 0)
        self.post(self.admin, "/api/assets/scan/receive", {
            "raw": raw, "item_id": identity, "campus_id": self.campus,
            "quantity": 2, "receipt_version": 0, "reason": "重复扫描",
        }, 409)
        self.post(self.member, "/api/assets/operations", {
            "action": "borrow", "item_id": identity, "campus_id": self.campus,
            "quantity": 1, "reason": "合成包装借用",
        })

    def test_supplier_model_candidates_do_not_write_inventory(self):
        match = self.save("items", {
            "name": "PR52候选型号", "specification": "R-42", "kind": "consumable", "unit": "颗",
        })
        other = self.save("items", {
            "name": "PR52不同型号", "specification": "R-43", "kind": "consumable", "unit": "颗",
        })
        lookup_data = {"configured": True, "status": "found",
                       "product": {"code": "C34567", "name": "电阻", "model": " r-42 "}}
        with patch("modules.asset_scan.lookup_lcsc", return_value=lookup_data) as mocked:
            value = self.member.post("/api/assets/scan/resolve", json={"raw": "C34567"}).get_json()
            self.assertTrue(value["ok"])
            self.assertIsNone(value["item"])
            self.assertEqual([item["id"] for item in value["candidates"]], [match])
            self.assertNotIn(other, [item["id"] for item in value["candidates"]])
            self.assertEqual(value["supplier_lookup"]["status"], "found")
            mocked.assert_called_once_with("C34567")
            self.post(self.admin, "/api/assets/identifiers", {
                "item_id": match, "code": "C34567", "reason": "合成映射",
            })
            resolved = self.member.post("/api/assets/scan/resolve", json={"raw": "C34567"}).get_json()
            self.assertEqual(resolved["item"]["id"], match)
            self.assertEqual(resolved["candidates"], [])


if __name__ == "__main__":
    unittest.main()
