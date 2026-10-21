import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import test_assets as fixture
from test_tasks import app_module


class UnitsTest(unittest.TestCase):
    setUp = fixture.AssetsTest.setUp
    client = fixture.AssetsTest.client
    post = fixture.AssetsTest.post
    save = fixture.AssetsTest.save

    def receive(self, count=3):
        item = self.save("items", dict(name="设备" + uuid4().hex, kind="returnable", unit="台"))
        r = self.post(
            self.admin,
            "/api/assets/units/receive",
            dict(item_id=item, campus_id=self.campus, quantity=count, reason="合成实物"),
        )
        return item, r["unit_ids"]

    def get_units(self, item):
        return self.admin.get("/api/assets/units?item_id=" + str(item)).get_json()["units"]

    def operate(self, action, units, client=None, status=200, **extra):
        return self.post(
            client or self.admin,
            "/api/assets/units/operate",
            dict(
                action=action, units=[dict(id=u["id"], version=u["version"]) for u in units], reason="合成操作", **extra
            ),
            status,
        )

    def test_new_item_counts_and_legacy_write_guard(self):
        item, _ = self.receive()
        units = self.get_units(item)
        self.assertEqual(len(units), 3)
        scanned = self.member.post("/api/assets/scan/resolve", json={"raw": "U:" + str(units[0]["id"])}).get_json()
        self.assertEqual(scanned["unit"]["item_name"], scanned["item"]["name"])
        self.post(
            self.member,
            "/api/assets/units/receive",
            dict(item_id=item, campus_id=self.campus, quantity=1, reason="无权限"),
            403,
        )
        self.post(
            self.member,
            "/api/assets/operations",
            dict(item_id=item, campus_id=self.campus, action="adjust", quantity=99, version=0, reason="旧客户端"),
            409,
        )
        self.operate("borrow", units[:2], self.member)
        self.assertEqual(len([u for u in self.get_units(item) if u["keeper_id"] == self.member_id]), 2)
        self.operate("return", self.get_units(item)[:1], self.member, campus_id=self.other, health="fault")
        returned = self.get_units(item)[0]
        self.assertEqual(
            (returned["campus_id"], returned["health"], returned["keeper_id"]), (self.other, "inspection", None)
        )
        self.operate("borrow", [returned], self.member, status=409)
        self.operate("healthy", [returned])
        self.operate("borrow", self.get_units(item)[:1], self.member)

    def test_bulk_conflict_rolls_back_and_handoff_requires_receiver(self):
        item, _ = self.receive()
        units = self.get_units(item)
        self.operate("borrow", units[1:2], self.member)
        self.operate("borrow", units, self.member, status=409)
        self.assertIsNone(self.get_units(item)[0]["keeper_id"])
        held = self.get_units(item)[1:2]
        self.operate("handoff", held, self.member, member_id=self.admin_id)
        self.operate("accept", self.get_units(item)[1:2], self.member, status=409)
        self.operate("accept", self.get_units(item)[1:2], self.admin)
        self.assertEqual(self.get_units(item)[1]["keeper_id"], self.admin_id)

    def test_handoff_invalidated_by_move(self):
        item, _ = self.receive(1)
        self.operate("borrow", self.get_units(item), self.member)
        self.operate("handoff", self.get_units(item), self.member, member_id=self.admin_id)
        self.operate("move", self.get_units(item), self.member, campus_id=self.other)
        self.operate("accept", self.get_units(item), self.admin, status=409)

    def test_lost_recovery_does_not_reopen_loan(self):
        item, _ = self.receive(1)
        self.operate("borrow", self.get_units(item), self.member)
        self.operate("lost", self.get_units(item), self.member, status=403)
        self.operate("lost", self.get_units(item))
        self.operate("recover", self.get_units(item))
        unit = self.get_units(item)[0]
        self.assertEqual((unit["lifecycle"], unit["health"], unit["keeper_id"]), ("active", "inspection", None))
        history = self.admin.get(f"/api/assets/units/{unit['id']}/history").get_json()
        self.assertEqual(history["loans"][0]["closed_reason"], "lost")
        self.operate("retire", [unit], destroyed=True)
        self.operate("recover", self.get_units(item), status=409)

    def test_concurrent_same_unit_cannot_double_borrow(self):
        item, _ = self.receive(1)
        unit = self.get_units(item)[0]
        clients = [self.client(self.member_id), self.client(self.admin_id)]

        def submit(c):
            return c.post(
                "/api/assets/units/operate",
                json=dict(
                    request_id=uuid4().hex,
                    action="borrow",
                    units=[dict(id=unit["id"], version=unit["version"])],
                    reason="并发",
                ),
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(submit, clients)), [200, 409])

    def test_migration_requires_every_old_loan_and_keeps_history(self):
        self.post(
            self.admin,
            "/api/assets/operations",
            dict(action="receive", item_id=self.item, campus_id=self.campus, quantity=3, reason="旧库存"),
        )
        loan = self.post(
            self.member,
            "/api/assets/operations",
            dict(action="borrow", item_id=self.item, campus_id=self.campus, quantity=1, reason="旧借单"),
        )["loan_id"]

        def migrate(action, **extra):
            extra.setdefault("version", self.admin.get(f"/api/assets/items/{self.item}").get_json()["item"]["version"])
            return self.post(
                self.admin,
                "/api/assets/units/migrate",
                dict(action=action, item_id=self.item, reason="人工核对", **extra),
            )

        version = self.admin.get(f"/api/assets/items/{self.item}").get_json()["item"]["version"]
        migrate("start", version=version)
        self.post(
            self.member, "/api/assets/operations", dict(action="return", loan_id=loan, quantity=1, reason="冻结"), 409
        )
        migrate("add", campus_id=self.campus, quantity=2)
        self.post(
            self.admin,
            "/api/assets/units/migrate",
            dict(
                action="finish",
                item_id=self.item,
                version=self.admin.get(f"/api/assets/items/{self.item}").get_json()["item"]["version"],
                reason="漏借单",
            ),
            409,
        )
        migrate("add", campus_id=self.campus, quantity=1, loan_id=loan)
        migrate("finish")
        units = self.get_units(self.item)
        self.assertEqual(len(units), 3)
        self.assertEqual([u["keeper_id"] for u in units if u["keeper_id"]], [self.member_id])
        with app_module.app.app_context():
            self.assertEqual(
                app_module.get_db().execute("SELECT outstanding FROM asset_loans WHERE id=?", (loan,)).fetchone()[0], 1
            )
