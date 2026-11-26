"""Catalog and scanned inventory must commit together, using synthetic labels."""
import unittest
from uuid import uuid4

import test_assets as fixture


class AssetEditorTest(unittest.TestCase):
    setUp = fixture.AssetsTest.setUp
    client = fixture.AssetsTest.client
    post = fixture.AssetsTest.post
    save = fixture.AssetsTest.save
    stock = fixture.AssetsTest.stock

    def scanned(self, **changes):
        return dict({"raw": "{on:TEST-NEW,pc:C9898,pm:SYNTHETIC,qty:3,mc:}",
                     "name": "合成扫码新物料", "specification": "SYNTHETIC", "kind": "consumable",
                     "unit": "颗", "quantity": 3, "campus_id": self.campus,
                     "reason": "核对实收", "receipt_version": 0}, **changes)

    def test_create_receive_quantity_units_and_retry(self):
        for kind in ("consumable", "returnable"):
            code = "C9898" if kind == "consumable" else "C9899"
            data = self.scanned(kind=kind, name="合成" + kind,
                                raw=f"{{pc:{code},qty:3}}", request_id=uuid4().hex)
            first = self.post(self.admin, "/api/assets/scan/create-receive", data)
            self.assertEqual(first, self.post(self.admin, "/api/assets/scan/create-receive", data))
            self.assertEqual(self.stock(first["item_id"])["available"], 3)
            if kind == "returnable":
                self.assertEqual(len(first["unit_ids"]), 3)

    def test_failed_receipt_rolls_back_catalog_campus_and_mapping(self):
        data = self.scanned(campus_id=0, new_campus="合成新建校区", quantity=0)
        self.post(self.admin, "/api/assets/scan/create-receive", data, 400)
        catalog = self.admin.get("/api/assets/catalog").get_json()
        self.assertNotIn("合成新建校区", [c["name"] for c in catalog["campuses"]])
        resolved = self.admin.post("/api/assets/scan/resolve", json={"raw": data["raw"]}).get_json()
        self.assertIsNone(resolved["item"])
        self.post(self.member, "/api/assets/scan/create-receive", self.scanned(), 403)
        result = self.post(self.admin, "/api/assets/scan/create-receive", {**data, "quantity": 2})
        detail = self.admin.get(f'/api/assets/items/{result["item_id"]}').get_json()
        self.assertEqual(sum(c["available"] for c in detail["campuses"]), 2)
        events = self.admin.get(f'/api/assets/events?item_id={result["item_id"]}').get_json()["events"]
        receipt = next(e["receipt"] for e in events if e.get("receipt"))
        self.assertEqual((receipt["label_quantity"], receipt["actual_quantity"]), (3, 2))

    def test_edit_inventory_stale_version_rolls_back_metadata(self):
        data = dict(id=self.screw, name="合成新名称", kind="consumable", unit="颗", version=1,
                    inventory=dict(campus_id=self.campus, quantity=20, version=0, reason="合成盘点"))
        self.post(self.member, "/api/assets/catalog/items", data, 403)
        self.post(self.admin, "/api/assets/catalog/items", data)
        self.assertEqual(self.stock(self.screw)["available"], 20)
        self.post(self.admin, "/api/assets/catalog/items", {**data, "version": 2, "name": "不应保存"}, 409)
        item = self.admin.get(f"/api/assets/items/{self.screw}").get_json()["item"]
        self.assertEqual((item["name"], item["version"]), ("合成新名称", 2))
        second = {**data, "version": 2, "unit": "枚", "reason": "更正计量单位名称",
                  "inventory": dict(campus_id=self.other, quantity=5, version=0, reason="另一校区盘点")}
        self.post(self.admin, "/api/assets/catalog/items", second)
        self.assertEqual(self.stock(self.screw)["available"], 20)
        self.assertEqual(self.stock(self.screw, self.other)["available"], 5)

    def test_edit_rejects_unit_count_override_and_conflicting_campuses(self):
        item = self.save("items", dict(name="合成单件", unit="台", kind="returnable"))
        data = dict(id=item, name="合成单件", unit="台", kind="returnable", version=1,
                    inventory=dict(campus_id=self.campus, quantity=20, version=0, reason="合成测试"))
        self.post(self.admin, "/api/assets/catalog/items", data, 409)
        data["inventory"]["new_campus"] = "不能同时指定"
        self.post(self.admin, "/api/assets/catalog/items", data, 400)
