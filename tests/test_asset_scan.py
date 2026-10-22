"""Synthetic purchase labels; no user order numbers or real inventory."""

import unittest
from unittest.mock import patch
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import test_assets as fixture
from modules.asset_scan import parse_label
from modules.assets import Invalid


class ScanTest(unittest.TestCase):
    setUp = fixture.AssetsTest.setUp
    client = fixture.AssetsTest.client
    post = fixture.AssetsTest.post
    save = fixture.AssetsTest.save

    def receipt(self, **extra):
        return dict(
            raw="{on:TEST-ORDER,pc:C12345,pm:TEST-RESISTOR,qty:100,mc:,cc:1,pdi:TEST,hp:1}",
            item_id=self.screw,
            campus_id=self.campus,
            quantity=70,
            reason="合成实收",
            receipt_version=0,
            **extra,
        )

    def bind(self):
        self.post(self.admin, "/api/assets/identifiers", dict(item_id=self.screw, code="C12345", reason="合成映射"))

    def test_parser_empty_unknown_and_invalid_quantity(self):
        value = parse_label(self.receipt()["raw"])
        self.assertEqual(value["label_quantity"], 100)
        self.assertEqual(value["fields"]["mc"], "")
        self.assertEqual(value["fields"]["pdi"], "TEST")
        for quantity in ("0", "-2", "1.5", "3oops", ""):
            self.assertIsNone(parse_label("{pc:C12345,qty:" + quantity + "}")["label_quantity"])
        for raw in ("{pc:C12345,pc:C23456,qty:1}", "{pc:bad,qty:1}", '__import__("os")', "{pc:C1,bad}"):
            with self.assertRaises(Invalid):
                parse_label(raw)

    def test_resolve_and_permissions(self):
        with patch("modules.asset_scan.lookup_lcsc", return_value={
            "configured": True, "status": "unavailable", "product": None,
        }):
            r = self.member.post("/api/assets/scan/resolve", json={"raw": self.receipt()["raw"]}).get_json()
        self.assertIsNone(r["item"])
        self.assertTrue(r["supplier_lookup"]["configured"])
        self.assertEqual(r["supplier_lookup"]["status"], "unavailable")
        self.post(self.member, "/api/assets/identifiers", dict(item_id=self.screw, code="C12345", reason="测试"), 403)
        self.bind()
        with patch("modules.asset_scan.lookup_lcsc", return_value={
            "configured": True, "status": "unavailable", "product": None,
        }):
            r = self.member.post("/api/assets/scan/resolve", json={"raw": "C12345"}).get_json()
        self.assertEqual(r["item"]["id"], self.screw)

    def test_receipt_idempotency_and_history(self):
        self.bind()
        data = dict(request_id=uuid4().hex, **self.receipt())
        first = self.post(self.member, "/api/assets/scan/receive", data)
        self.assertEqual(first, self.post(self.member, "/api/assets/scan/receive", data))
        self.post(self.member, "/api/assets/scan/receive", self.receipt(), 409)
        next_data = self.receipt()
        next_data["receipt_version"] = first["receipt_id"]
        self.post(self.member, "/api/assets/scan/receive", next_data, 400)
        next_data["duplicate_reason"] = "另一袋具有相同标签，已核对"
        self.post(self.member, "/api/assets/scan/receive", next_data)
        detail = self.member.get(f"/api/assets/items/{self.screw}").get_json()
        self.assertEqual(next(c["available"] for c in detail["campuses"] if c["id"] == self.campus), 140)
        records = self.member.get(f"/api/assets/events?item_id={self.screw}").get_json()["events"]
        source = next(e["receipt"] for e in records if e.get("receipt"))
        self.assertEqual(source["raw"], data["raw"])
        self.assertEqual(source["actual_quantity"], 70)
        self.assertEqual(source["parsed"]["fields"]["mc"], "")

    def test_concurrent_same_label_requires_recheck(self):
        self.bind()
        clients = [self.client(self.member_id), self.client(self.admin_id)]

        def submit(client):
            return client.post(
                "/api/assets/scan/receive", json=dict(request_id=uuid4().hex, **self.receipt())
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(submit, clients)), [200, 409])

    def test_batch_repeated_and_unknown_packages_are_atomic(self):
        self.bind()
        self.post(self.member, "/api/assets/scan/batch", {"receipts": [self.receipt(), self.receipt()]}, 409)
        bad = self.receipt()
        bad["raw"] = "{on:TEST-OTHER,pc:C99999,qty:1}"
        self.post(self.member, "/api/assets/scan/batch", {"receipts": [self.receipt(), bad]}, 409)
        d = self.member.get(f"/api/assets/items/{self.screw}").get_json()
        self.assertEqual(sum(c["available"] for c in d["campuses"]), 0)
        second = self.receipt()
        second["raw"] = second["raw"].replace("TEST-ORDER", "TEST-OTHER")
        response = self.post(self.member, "/api/assets/scan/batch", {"receipts": [self.receipt(), second]})
        self.assertEqual(len(response["receipts"]), 2)
