import unittest
import sqlite3
from io import BytesIO
import pdfplumber
import test_asset_units as fixture
from test_tasks import app_module
from modules.assets import migrate_assets


class LifecycleTest(unittest.TestCase):
    setUp = fixture.UnitsTest.setUp
    client = fixture.UnitsTest.client
    post = fixture.UnitsTest.post
    save = fixture.UnitsTest.save
    receive = fixture.UnitsTest.receive
    get_units = fixture.UnitsTest.get_units
    operate = fixture.UnitsTest.operate

    def object(self):
        return self.post(self.admin, "/api/assets/units/objects", dict(name="合成步兵", reason="测试"))["id"]

    def test_install_retains_loan_detach_return_repair_and_restore(self):
        item, _ = self.receive(2)
        obj = self.object()
        self.operate("borrow", self.get_units(item), self.member)
        self.operate("install", self.get_units(item), self.member, object_id=obj, position="左前驱动")
        self.assertTrue(all(u["keeper_id"] == self.member_id and u["object_id"] == obj for u in self.get_units(item)))
        self.operate("return", self.get_units(item), self.member, status=409, campus_id=self.other)
        self.operate("repair", self.get_units(item), status=409)
        self.operate("repair", self.get_units(item), detached=True)
        self.operate("return", self.get_units(item), self.member, campus_id=self.other)
        self.assertTrue(all(u["health"] == "inspection" and u["keeper_id"] is None for u in self.get_units(item)))
        self.operate("healthy", self.get_units(item), self.member, status=403)
        self.operate("healthy", self.get_units(item))

    def test_object_move_detects_new_install_and_keeps_custody(self):
        item, _ = self.receive(2)
        obj = self.object()
        self.operate("borrow", self.get_units(item), self.member)
        self.operate("install", self.get_units(item)[:1], object_id=obj)
        selected = [dict(id=u["id"], version=u["version"]) for u in self.get_units(item)[:1]]
        self.operate("install", self.get_units(item)[1:], object_id=obj)
        self.operate("move", self.get_units(item)[:1], status=409, campus_id=self.other, object_id=obj)
        self.operate("move", self.get_units(item), self.member, status=409, campus_id=self.other, object_id=obj)
        data = dict(
            object_id=obj, version=1, units=selected, campus_id=self.other, reason="整车移动", location="实验室"
        )
        self.post(self.admin, "/api/assets/units/object-move", data, 409)
        data["units"] = [dict(id=u["id"], version=u["version"]) for u in self.get_units(item)]
        self.post(self.member, "/api/assets/units/object-move", data, 403)
        self.post(self.admin, "/api/assets/units/object-move", data)
        self.assertTrue(
            all(u["campus_id"] == self.other and u["keeper_id"] == self.member_id for u in self.get_units(item))
        )
        self.post(
            self.admin,
            "/api/assets/units/objects",
            dict(id=obj, version=2, name="合成步兵", active=False, reason="停用"),
            409,
        )

    def test_stocktake_freezes_scope_missing_is_not_loss(self):
        item, ids = self.receive(3)
        self.operate("borrow", self.get_units(item)[:1], self.member)
        take = self.post(
            self.admin,
            "/api/assets/units/stocktakes",
            dict(campus_id=self.campus, item_ids=[item], scope="warehouse", reason="小范围盘点"),
        )["id"]
        self.operate("borrow", self.get_units(item)[1:], self.member, status=409)
        self.post(
            self.admin,
            "/api/assets/units/receive",
            dict(item_id=item, campus_id=self.campus, quantity=1, reason="冻结"),
            409,
        )
        self.post(
            self.admin,
            "/api/assets/units/stocktakes",
            dict(campus_id=self.campus, item_ids=[item], scope="all", reason="重叠"),
            409,
        )
        initial = self.admin.get(f"/api/assets/units/stocktakes/{take}").get_json()
        self.assertEqual(len(initial["units"]), 2)
        self.post(
            self.admin,
            "/api/assets/units/stocktakes/scan-batch",
            dict(take_id=take, unit_ids=ids[:2], reason="看到实物"),
        )
        detail = self.admin.get(f"/api/assets/units/stocktakes/{take}").get_json()
        self.assertEqual(len(detail["unexpected"]), 1)
        self.post(
            self.admin,
            "/api/assets/units/stocktakes/record",
            dict(take_id=take, action="close", revision=initial["revision"], reason="过期"),
            409,
        )
        self.post(
            self.admin,
            "/api/assets/units/stocktakes/record",
            dict(take_id=take, action="close", revision=detail["revision"], reason="漏扫待实物核查"),
        )
        self.assertTrue(all(u["lifecycle"] == "active" for u in self.get_units(item)))
        self.assertEqual(self.admin.get("/api/assets/units/catalog").get_json()["takes"][0]["status"], "closed")
        self.operate("borrow", self.get_units(item)[1:], self.member)

    def test_pdf_dimensions_identity_and_invalid_input(self):
        item, ids = self.receive(2)
        response = self.member.get(
            "/api/assets/labels.pdf?ids=" + ",".join(map(str, ids)) + "&paper=thermal&width=60&height=35"
        )
        self.assertEqual(response.status_code, 200)
        with pdfplumber.open(BytesIO(response.data)) as pdf:
            self.assertEqual(len(pdf.pages), 2)
            self.assertAlmostEqual(pdf.pages[0].width, 60 * 72 / 25.4, places=3)
            self.assertIn("U:" + str(ids[0]), pdf.pages[0].extract_text())
        for query in ("ids=1&width=nan", "ids=bad", "ids=1&height=0", "ids=1&kind=bad"):
            self.assertEqual(self.member.get("/api/assets/labels.pdf?" + query).status_code, 400)
        response = self.member.get(f"/api/assets/labels.pdf?kind=item&ids={item}&paper=a4")
        with pdfplumber.open(BytesIO(response.data)) as pdf:
            self.assertIn("M:" + str(item), pdf.pages[0].extract_text())

    def test_history_pagination_keeps_earliest_identity_event(self):
        item, ids = self.receive(1)
        for i in range(101):
            self.operate("move", self.get_units(item), campus_id=self.campus, location=str(i))
        first = self.member.get(f"/api/assets/units/{ids[0]}/history").get_json()
        second = self.member.get(f"/api/assets/units/{ids[0]}/history?page=2").get_json()
        self.assertEqual(first["total"], 102)
        self.assertEqual(len(first["events"]), 100)
        self.assertEqual(len(second["events"]), 2)
        self.assertEqual(second["events"][-1]["action"], "unit_receive")

    def test_repeated_migration_and_backup_preserve_identities_and_receipts(self):
        item, ids = self.receive(2)
        self.operate("borrow", self.get_units(item)[:1], self.member)
        with app_module.app.app_context():
            db = app_module.get_db()
            migrate_assets(db)
            migrate_assets(db)
            snapshot = sqlite3.connect(":memory:")
            try:
                db.backup(snapshot)
                self.assertEqual(
                    snapshot.execute("SELECT id FROM asset_units WHERE item_id=? ORDER BY id", (item,)).fetchall(),
                    [(i,) for i in ids],
                )
                self.assertEqual(
                    snapshot.execute("SELECT COUNT(*) FROM asset_unit_loans WHERE closed_at IS NULL").fetchone()[0], 1
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    snapshot.execute("DELETE FROM asset_events WHERE action='unit_borrow'")
            finally:
                snapshot.close()
