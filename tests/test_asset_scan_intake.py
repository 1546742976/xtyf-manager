"""Synthetic receiving drafts, external serials and ambiguous code regression."""

import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_assets as fixture
from test_tasks import app_module


class ScanIntakeTest(unittest.TestCase):
    setUp = fixture.AssetsTest.setUp
    client = fixture.AssetsTest.client
    post = fixture.AssetsTest.post
    save = fixture.AssetsTest.save
    stock = fixture.AssetsTest.stock

    def resolve(self, raw):
        with patch("modules.asset_scan.lookup_lcsc", return_value={"status": "unavailable", "product": None}):
            response = self.admin.post("/api/assets/scan/resolve", json={"raw": raw})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def entry(self, raw, **changes):
        value = self.resolve(raw)
        data = dict(raw=value["parsed"]["raw"], entry_id=uuid4().hex, quantity=1,
                    campus_id=self.campus, reason="合成采购核对", receipt_version=value["receipt_version"],
                    match_version=value["match_version"])
        if value["draft"]:
            data.update(draft=value["draft"], draft_id=value["draft_id"], code_role="item")
        if value["item"]:
            data["item_id"] = value["item"]["id"]
        return dict(data, **changes)

    def motor(self, name="合成厂家电机"):
        return dict(name=name, specification="MOTOR-SYNTHETIC", unit="台", kind="returnable",
                    category="", returnable_mode="unit")

    def batch(self, receipts, status=200, **data):
        return self.post(self.admin, "/api/assets/scan/batch", dict(receipts=receipts, **data), status)

    def count(self, table):
        with app_module.app.app_context():
            return app_module.get_db().execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_auto_drafts_share_one_catalog_and_commit_only_on_confirmation(self):
        before = self.count("asset_items")
        first = self.entry("{on:SYNTHETIC-A,pc:C990001,pm:RESISTOR-SYNTHETIC,qty:3}", quantity=3)
        second = self.entry("{on:SYNTHETIC-B,pc:C990001,pm:RESISTOR-SYNTHETIC,qty:4}", quantity=4)
        self.assertEqual(self.count("asset_items"), before)
        self.assertEqual(first["draft_id"], second["draft_id"])
        self.assertEqual(first["draft"]["kind"], "consumable")
        self.assertEqual(first["draft"]["unit"], "个")
        request_id = uuid4().hex
        with patch("modules.asset_scan.lookup_lcsc") as lookup:
            received = self.batch([first, second], request_id=request_id)
            self.assertEqual(received, self.batch([first, second], request_id=request_id))
            lookup.assert_not_called()
        self.assertEqual(self.count("asset_items"), before + 1)
        item_id = received["receipts"][0]["item_id"]
        self.assertEqual(item_id, received["receipts"][1]["item_id"])
        self.assertEqual(self.stock(item_id)["available"], 7)
        self.assertEqual(self.resolve("C990001")["item"]["id"], item_id)

    def test_remote_metadata_priority_and_invalid_label_quantity(self):
        raw = "{pc:C990002,pm:LABEL-MODEL,qty:oops}"
        with patch("modules.asset_scan.lookup_lcsc", return_value={
            "product": {"name": "合成商城名称", "model": "REMOTE-MODEL"}, "status": "found",
        }):
            value = self.admin.post("/api/assets/scan/resolve", json={"raw": raw}).get_json()
        self.assertEqual(value["draft"]["name"], "合成商城名称")
        self.assertEqual(value["draft"]["specification"], "REMOTE-MODEL")
        self.assertIsNone(value["parsed"]["label_quantity"])
        before = self.count("asset_items")
        self.batch([self.entry(raw, quantity=None)], 400)
        self.assertEqual(self.count("asset_items"), before)

    def test_arbitrary_raw_is_preserved_and_never_looked_up_as_a_url(self):
        with patch("modules.asset_scan.lookup_lcsc") as lookup:
            value = self.admin.post("/api/assets/scan/resolve", json={"raw": "  https://example.invalid/AbC  "}).get_json()
            lookup.assert_not_called()
        self.assertEqual(value["parsed"]["raw"], "https://example.invalid/AbC")
        self.assertEqual(value["status"], "unknown")
        self.assertIsNone(value["draft"])
        broken = self.resolve("{pc:bad,qty:2}")
        self.assertEqual(broken["status"], "parse_error")
        self.assertIn("parse_error", broken["parsed"])

    def test_grouped_serial_drafts_create_one_model_and_distinct_physical_units(self):
        entries = [self.entry("SYNTHETIC-MOTOR-" + suffix, draft_id="motor-group", draft=self.motor(),
                              intent="new_item", code_role="serial") for suffix in ("A", "B")]
        result = self.batch(entries)["receipts"]
        self.assertEqual(result[0]["item_id"], result[1]["item_id"])
        self.assertNotEqual(result[0]["unit_ids"], result[1]["unit_ids"])
        for source, received in zip(entries, result):
            resolved = self.resolve(source["raw"])
            self.assertEqual(resolved["unit"]["id"], received["unit_ids"][0])
            self.assertEqual(resolved["unit"]["serial"], source["raw"])
        before = self.count("asset_units")
        repeated = self.entry(entries[0]["raw"], duplicate_reason="重复扫描核对")
        self.batch([repeated], 409)
        self.assertEqual(self.count("asset_units"), before)

    def test_existing_model_accepts_new_unknown_manufacturer_serial(self):
        item_id = self.save("items", self.motor())
        entry = self.entry("SYNTHETIC-MODEL-EXISTING", target_type="item", target_id=item_id,
                           item_id=item_id, intent="new_unit", code_role="serial")
        result = self.batch([entry])["receipts"][0]
        self.assertEqual(result["item_id"], item_id)
        self.assertEqual(len(result["unit_ids"]), 1)
        self.assertEqual(self.resolve(entry["raw"])["unit"]["id"], result["unit_ids"][0])

    def test_serial_quantity_and_late_failure_roll_back_models_aliases_and_stock(self):
        before = tuple(self.count(t) for t in ("asset_items", "asset_units", "asset_code_aliases", "asset_receipts"))
        first = self.entry("SYNTHETIC-GOOD", draft_id="good", draft=self.motor(), intent="new_item", code_role="serial")
        second = self.entry("SYNTHETIC-BAD", draft_id="bad", draft=self.motor("合成应回滚"),
                            intent="new_item", code_role="serial", quantity=2)
        self.batch([first, second], 400)
        self.assertEqual(tuple(self.count(t) for t in ("asset_items", "asset_units", "asset_code_aliases", "asset_receipts")), before)
        second["quantity"], second["campus_id"] = 1, 999999
        self.batch([first, second], 404)
        self.assertEqual(tuple(self.count(t) for t in ("asset_items", "asset_units", "asset_code_aliases", "asset_receipts")), before)

    def test_internal_item_code_can_also_identify_new_external_item(self):
        raw = "M:" + str(self.screw)
        entry = self.entry(raw, draft_id="collision", draft=dict(name="合成螺丝", specification="", unit="颗",
                           kind="consumable", category=""), intent="new_item", force_new=True,
                           code_role="item", conflict_reason="两种体系的编号相同，确认另建")
        entry.pop("item_id")
        result = self.batch([entry])["receipts"][0]
        value = self.resolve(raw)
        self.assertEqual(value["status"], "ambiguous")
        self.assertIsNone(value["item"])
        self.assertEqual({m["target_id"] for m in value["matches"]}, {self.screw, result["item_id"]})
        legacy = dict(raw=raw, item_id=self.screw, campus_id=self.campus, quantity=1,
                      reason="旧调用不能自动选择", receipt_version=value["receipt_version"], duplicate_reason="已核对")
        self.post(self.admin, "/api/assets/scan/receive", legacy, 409)
        selected = dict(legacy, target_type="item", target_id=self.screw, match_version=value["match_version"])
        self.batch([selected])
        self.assertEqual(self.stock(self.screw)["available"], 1)

    def test_supplier_and_internal_unit_collision_union_all_targets(self):
        self.post(self.admin, "/api/assets/identifiers", dict(item_id=self.screw, code="C990003", reason="合成关联"))
        motor = self.motor()
        supplier_serial = self.entry("C990003", draft_id="supplier-collision", draft=motor, intent="new_item",
                                     code_role="serial", conflict_reason="厂家序列号和商品编号相同")
        supplier_serial.pop("item_id")
        first = self.batch([supplier_serial])["receipts"][0]
        value = self.resolve("C990003")
        self.assertEqual({m["target_type"] for m in value["matches"]}, {"item", "unit"})
        raw = "U:" + str(first["unit_ids"][0])
        another = self.entry(raw, draft_id="unit-collision", draft=self.motor("合成另一型号"), intent="new_item",
                             code_role="serial", conflict_reason="厂家码与系统单件码同文")
        another.pop("item_id")
        second = self.batch([another])["receipts"][0]
        value = self.resolve(raw)
        self.assertEqual({m["target_id"] for m in value["matches"]}, {first["unit_ids"][0], second["unit_ids"][0]})

    def test_changed_mapping_requires_review_before_any_draft_is_saved(self):
        waiting = self.entry("{pc:C990004,pm:STALE-SYNTHETIC,qty:1}")
        self.post(self.admin, "/api/assets/identifiers", dict(item_id=self.screw, code="C990004", reason="另一会话关联"))
        before = self.count("asset_items")
        self.batch([waiting], 409)
        self.assertEqual(self.count("asset_items"), before)
        self.assertEqual(self.stock(self.screw)["available"], 0)

    def test_explicit_same_code_new_units_share_prebatch_revision(self):
        item_id = self.save("items", self.motor())
        one = self.entry("SYNTHETIC-SAME-SERIAL", item_id=item_id, intent="new_unit", code_role="serial")
        two = dict(one, entry_id=uuid4().hex, conflict_reason="确认为另一台重码电机")
        result = self.batch([one, two])["receipts"]
        self.assertNotEqual(result[0]["unit_ids"], result[1]["unit_ids"])
        value = self.resolve(one["raw"])
        self.assertEqual(len(value["matches"]), 2)
        before = self.count("asset_units")
        again = self.entry(one["raw"], item_id=item_id, target_type="unit", target_id=result[0]["unit_ids"][0],
                           intent="new_unit", code_role="serial", duplicate_reason="核对收货历史",
                           conflict_reason="确认第三台同码实物")
        self.batch([again])
        self.assertEqual(self.count("asset_units"), before + 1)

    def test_new_drafts_and_bindings_require_manager(self):
        unknown = self.entry("{pc:C990005,qty:1}")
        self.post(self.member, "/api/assets/scan/batch", dict(receipts=[unknown]), 403)
        existing = self.entry("SYNTHETIC-BIND", item_id=self.screw, code_role="item", intent="bind")
        self.post(self.member, "/api/assets/scan/batch", dict(receipts=[existing]), 403)
        self.assertEqual(self.resolve("SYNTHETIC-BIND")["matches"], [])

    def test_collisions_and_new_binding_paths_cannot_omit_recheck_token(self):
        raw = "M:" + str(self.screw)
        entry = self.entry(raw, draft_id="another", draft=dict(name="合成独立档案", unit="个", kind="consumable"),
                           intent="new_item", conflict_reason="确认为另一物品")
        entry.pop("item_id")
        before = self.count("asset_items")
        without_intent = {k: v for k, v in entry.items() if k not in ("intent", "match_version", "conflict_reason")}
        self.batch([without_intent], 409)
        without_version = {k: v for k, v in entry.items() if k != "match_version"}
        self.batch([without_version], 409)
        self.assertEqual(self.count("asset_items"), before)

    def test_new_same_supplier_code_different_drafts_requires_explicit_collision(self):
        one = self.entry("{pc:C990006,on:SYNTHETIC-ONE,qty:1}")
        two = self.entry("{pc:C990006,on:SYNTHETIC-TWO,qty:1}", draft_id="different",
                         draft=dict(name="合成另一物品", unit="个", kind="consumable"))
        before = self.count("asset_items")
        self.batch([one, two], 409)
        self.assertEqual(self.count("asset_items"), before)
        two.update(intent="new_item", conflict_reason="同批采购品编号跨体系重码")
        self.batch([one, two])
        self.assertEqual(len(self.resolve("C990006")["matches"]), 2)

    def test_original_serial_supports_existing_borrow_return_move_and_stocktake(self):
        raw = "SYNTHETIC-FLOW-SERIAL"
        self.batch([self.entry(raw, draft_id="flow", draft=self.motor(), intent="new_item", code_role="serial")])
        def operate(action, client=None, **extra):
            unit = self.resolve(raw)["unit"]
            return self.post(client or self.admin, "/api/assets/units/operate", dict(
                action=action, units=[dict(id=unit["id"], version=unit["version"])], reason="合成原码流转", **extra))
        operate("borrow", self.member)
        self.assertEqual(self.resolve(raw)["unit"]["keeper_id"], self.member_id)
        operate("move", self.member, campus_id=self.other)
        self.assertEqual(self.resolve(raw)["unit"]["campus_id"], self.other)
        operate("return", self.member, campus_id=self.other)
        unit = self.resolve(raw)["unit"]
        self.assertIsNone(unit["keeper_id"])
        take_id = self.post(self.admin, "/api/assets/units/stocktakes", dict(
            campus_id=self.other, item_ids=[unit["item_id"]], scope="warehouse", reason="合成厂家原码盘点"))["id"]
        self.post(self.admin, "/api/assets/units/stocktakes/scan-batch", dict(
            take_id=take_id, unit_ids=[self.resolve(raw)["unit"]["id"]], reason="扫描厂家原码"))
        take = self.admin.get(f"/api/assets/units/stocktakes/{take_id}").get_json()
        self.assertTrue(take["units"][0]["seen_at"])

    def test_duplicate_scan_requires_explicit_new_identity_and_unique_entry(self):
        item_id = self.save("items", self.motor())
        entry = self.entry("SYNTHETIC-DUPLICATE", item_id=item_id, intent="new_unit", code_role="serial")
        self.batch([entry, dict(entry, entry_id=uuid4().hex)], 409)
        self.assertEqual(self.resolve(entry["raw"])["matches"], [])

    def test_alias_target_constraint_and_per_target_uniqueness(self):
        item_id = self.save("items", self.motor())
        result = self.batch([self.entry("SYNTHETIC-CONSTRAINT", item_id=item_id,
                                       intent="new_unit", code_role="serial")])["receipts"][0]
        with app_module.app.app_context():
            db = app_module.get_db()
            alias = dict(db.execute("SELECT * FROM asset_code_aliases WHERE unit_id=?", (result["unit_ids"][0],)).fetchone())
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO asset_code_aliases(namespace,code,raw,item_id,unit_id,actor_id,created_at,reason) "
                           "VALUES(?,?,?,?,?,?,?,?)", tuple(alias[key] for key in
                           ("namespace", "code", "raw", "item_id", "unit_id", "actor_id", "created_at", "reason")))
            db.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO asset_code_aliases(namespace,code,raw,item_id,unit_id,actor_id,created_at,reason) "
                           "VALUES('raw','BOTH','BOTH',?,?,?,'2026-10-23','invalid')",
                           (item_id, result["unit_ids"][0], self.admin_id))
            db.rollback()
