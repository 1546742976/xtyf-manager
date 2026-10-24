"""Status consumables do not consume stock; supplier receipts keep exact quantities."""
import unittest
from uuid import uuid4
import test_assets as fixture
from test_tasks import app_module
from modules.assets import migrate_assets


class SupplyTest(unittest.TestCase):
    setUp = fixture.AssetsTest.setUp
    client = fixture.AssetsTest.client
    post = fixture.AssetsTest.post
    save = fixture.AssetsTest.save

    def status_item(self):
        return self.save("items", dict(name="合成状态螺丝", kind="consumable", unit="颗", consumable_mode="status"))

    def detail(self, item):
        return self.admin.get(f"/api/assets/items/{item}").get_json()

    def status_data(self, item, value="sufficient", campus=None):
        detail = self.detail(item)
        campus = next(c for c in detail["campuses"] if c["id"] == (campus or self.campus))
        return dict(item_id=item, campus_id=campus["id"], status=value,
                    item_version=detail["item"]["version"], version=campus["stock_version"])

    def edit_mode(self, item, mode):
        value = self.detail(item)["item"]
        return dict(id=item, version=value["version"], name=value["name"], specification=value["specification"],
                    kind=value["kind"], unit=value["unit"], consumable_mode=mode, reason="合成管理方式调整")

    def test_two_states_per_campus_member_write_and_retry(self):
        item = self.status_item()
        self.assertTrue(all(c["supply_status"] == "low" for c in self.detail(item)["campuses"]))
        body = dict(self.status_data(item), request_id=uuid4().hex)
        first = self.post(self.member, "/api/assets/supply-status", body)
        self.assertEqual(first, self.post(self.member, "/api/assets/supply-status", body))
        self.post(self.admin, "/api/assets/supply-status", self.status_data(item, "low"))
        self.post(self.member, "/api/assets/supply-status", {**body, "request_id": uuid4().hex}, 409)
        self.post(self.member, "/api/assets/supply-status", self.status_data(item))
        states = {c["id"]: c["supply_status"] for c in self.detail(item)["campuses"]}
        self.assertEqual((states[self.campus], states[self.other]), ("sufficient", "low"))
        events = self.member.get(f"/api/assets/events?item_id={item}").get_json()["events"]
        self.assertEqual(len([e for e in events if e["action"] == "supply_status"]), 3)
        self.assertTrue(all(e["quantity"] == 0 and e["actor_name"] for e in events))
        listed = self.member.get(f"/api/assets/items?campus_id={self.other}").get_json()["items"]
        value = next(i for i in listed if i["id"] == item)
        self.assertEqual(value["supplies"][0]["supply_status"], "low")

    def test_existing_count_history_preserved_and_old_writes_blocked(self):
        receipt = self.post(self.member, "/api/assets/operations", dict(action="receive", item_id=self.screw,
                            campus_id=self.campus, quantity=100, reason="合成旧库存"))
        edit = self.edit_mode(self.screw, "status")
        self.post(self.member, "/api/assets/catalog/items", edit, 403)
        self.post(self.admin, "/api/assets/catalog/items", edit)
        for action in ("receive", "consume", "adjust", "transfer"):
            self.post(self.admin, "/api/assets/operations", dict(action=action, item_id=self.screw,
                      campus_id=self.campus, target_campus_id=self.other, quantity=1, version=2, reason="旧客户端"), 409)
        self.post(self.admin, "/api/assets/operations", dict(action="correct", event_id=receipt["event_id"], reason="旧流水"), 409)
        with app_module.app.app_context():
            db = app_module.get_db()
            self.assertEqual(db.execute("SELECT available FROM asset_stock WHERE item_id=?", (self.screw,)).fetchone()[0], 100)
            migrate_assets(db)
            migrate_assets(db)
        self.assertEqual(self.detail(self.screw)["campuses"][0]["available"], 0)
        status_body = self.status_data(self.screw)
        self.post(self.admin, "/api/assets/catalog/items", self.edit_mode(self.screw, "quantity"))
        self.assertTrue(all(c["available"] == 0 for c in self.detail(self.screw)["campuses"]))
        records = self.member.get(f"/api/assets/events?item_id={self.screw}").get_json()["events"]
        reset = next(e for e in records if e["details"].get("mode_reset"))
        self.post(self.admin, "/api/assets/operations", dict(action="correct", event_id=reset["id"], reason="不应恢复旧数"), 409)
        self.post(self.admin, "/api/assets/operations", dict(action="correct", event_id=receipt["event_id"], reason="旧记录"), 409)
        self.post(self.admin, "/api/assets/catalog/items", self.edit_mode(self.screw, "status"))
        self.post(self.member, "/api/assets/supply-status", status_body, 409)
        records = self.member.get(f"/api/assets/events?item_id={self.screw}").get_json()["events"]
        self.assertTrue(any(e["details"].get("previous_stock", [{}])[0].get("available") == 100 for e in records))

    def test_supplier_items_keep_scan_and_quantity(self):
        self.post(self.admin, "/api/assets/identifiers", dict(item_id=self.screw, code="C12345", reason="合成绑定"))
        self.post(self.admin, "/api/assets/catalog/items", self.edit_mode(self.screw, "status"), 409)
        self.post(self.member, "/api/assets/scan/receive", dict(raw="{pc:C12345,qty:100}", item_id=self.screw,
                  campus_id=self.campus, quantity=80, receipt_version=0, reason="合成实收"))
        self.assertEqual(next(c for c in self.detail(self.screw)["campuses"] if c["id"] == self.campus)["available"], 80)
        item = self.status_item()
        self.post(self.admin, "/api/assets/identifiers", dict(item_id=item, code="C222", reason="不应绑定"), 409)
        self.post(self.admin, "/api/assets/catalog/items", {**self.edit_mode(item, "status"), "supplier_code": "C222"}, 409)
        self.post(self.member, "/api/assets/scan/receive", dict(raw="{pc:C222,qty:2}", item_id=item,
                  campus_id=self.campus, quantity=2, receipt_version=0, reason="不应计数"), 409)

    def test_invalid_modes_and_inactive_resources(self):
        self.post(self.admin, "/api/assets/catalog/items", dict(name="错误设备", kind="returnable", unit="个", consumable_mode="status"), 400)
        self.post(self.admin, "/api/assets/catalog/items", dict(name="错误耗材", kind="consumable", unit="个", consumable_mode="other"), 400)
        item = self.status_item()
        self.post(self.member, "/api/assets/supply-status", {**self.status_data(item), "status": "other"}, 400)
        self.post(self.member, "/api/assets/supply-status", {**self.status_data(item), "quantity": 1}, 400)
        quantity = self.status_data(item)
        quantity["item_id"] = self.screw
        self.post(self.member, "/api/assets/supply-status", quantity, 409)
        data = self.edit_mode(item, "status")
        self.post(self.admin, "/api/assets/catalog/items", {**data, "active": False})
        self.post(self.member, "/api/assets/supply-status", self.status_data(item), 409)
