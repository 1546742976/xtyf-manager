"""BOM spreadsheet preview and atomic quantity operations use synthetic materials."""
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from openpyxl import Workbook
from test_tasks import app_module


class AssetBomTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="xtyf-bom-")
        self.addCleanup(folder.cleanup)
        self.path = str(Path(folder.name) / "data.db")
        source = sqlite3.connect(app_module.DB_PATH)
        target = sqlite3.connect(self.path)
        source.backup(target)
        source.close()
        target.close()
        for name in ("common.DB_PATH", "app.DB_PATH", "modules.security.DB_PATH"):
            patched = patch(name, self.path)
            patched.start()
            self.addCleanup(patched.stop)
        with app_module.app.app_context():
            db = app_module.get_db()
            self.admin_id = db.execute(
                "INSERT INTO members(name,username,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                ("BOM管理员", uuid4().hex, "unused", "admin", "2026-10-26"),
            ).lastrowid
            self.member_id = db.execute(
                "INSERT INTO members(name,username,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                ("BOM成员", uuid4().hex, "unused", "member", "2026-10-26"),
            ).lastrowid
            db.commit()
        self.admin = self.client(self.admin_id)
        self.member = self.client(self.member_id)
        self.campus = self.post(self.admin, "/api/assets/catalog/campuses", {"name": "BOM测试校区"})["id"]
        self.part = self.post(self.admin, "/api/assets/catalog/items", {
            "name": "BOM测试电阻", "specification": "10k 0603", "unit": "颗", "kind": "consumable",
            "consumable_mode": "quantity", "supplier_code": "C98765432",
        })["id"]
        self.device = self.post(self.admin, "/api/assets/catalog/items", {
            "name": "BOM测试工具", "unit": "个", "kind": "returnable", "returnable_mode": "quantity",
        })["id"]
        self.post(self.admin, "/api/assets/operations", {
            "action": "receive", "item_id": self.part, "campus_id": self.campus,
            "quantity": 100, "reason": "合成初始入库",
        })

    def client(self, identity):
        client = app_module.app.test_client()
        with client.session_transaction() as session:
            session.update(uid=identity, session_version=0)
        return client

    def post(self, client, url, data, status=200, request_id=None):
        response = client.post(url, json={"request_id": request_id or uuid4().hex, **data})
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def detail(self, item_id=None):
        payload = self.member.get(f"/api/assets/items/{item_id or self.part}").get_json()
        return payload["item"], next(r for r in payload["campuses"] if r["id"] == self.campus)

    def line(self, item_id=None, qty=3):
        item, stock = self.detail(item_id)
        return {"item_id": item["id"], "version": item["version"],
                "stock_version": stock["stock_version"], "quantity": qty}

    def batch(self, rows, mode="outbound", sets=1, client=None, status=200, request_id=None):
        return self.post(client or self.member, "/api/assets/bom/commit", {
            "mode": mode, "campus_id": self.campus, "sets": sets,
            "reason": "合成BOM物料用量", "rows": rows,
        }, status, request_id)

    def preview(self, filename, content, mode="outbound", client=None, status=200):
        response = (client or self.member).post("/api/assets/bom/preview", data={
            "file": (io.BytesIO(content), filename),
            "mode": mode, "campus_id": str(self.campus),
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def test_csv_c_code_match_and_xlsx(self):
        csv_content = "嘉立创编号,物料名称,规格型号,每套数量,单位\nC98765432,BOM测试电阻,10k 0603,3,颗\n".encode()
        payload = self.preview("bom.csv", csv_content)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["rows"][0]["quantity"], 3)
        candidate = payload["rows"][0]["candidates"][0]
        self.assertEqual(candidate["id"], self.part)
        self.assertEqual(candidate["available"], 100)
        book = Workbook()
        page = book.active
        page.append(["元件名称", "型号", "每套数量"])
        page.append(["BOM测试电阻", "10k 0603", 6])
        result = io.BytesIO()
        book.save(result)
        preview = self.preview("bom.xlsx", result.getvalue())
        self.assertEqual(preview["rows"][0]["candidates"][0]["id"], self.part)

    def test_kits_and_duplicate_rows_are_aggregated(self):
        line = self.line(qty=4)
        duplicate = self.line(qty=3)
        result = self.batch([line, duplicate], sets=3)
        self.assertEqual(len(result["operations"]), 1)
        self.assertEqual(result["operations"][0]["quantity"], 21)
        self.assertEqual(self.detail()[1]["available"], 79)
        self.assertEqual(self.detail()[1]["consumed"], 21)

    def test_shortage_is_atomic_for_all_rows(self):
        before = self.detail()[1]["available"]
        result = self.batch([self.line(qty=40), self.line(self.device, qty=1)],
                            sets=3, status=409)
        self.assertIn("库存不足", result["error"])
        self.assertEqual(self.detail()[1]["available"], before)
        self.assertEqual(self.detail(self.device)[1]["borrowed"], 0)

    def test_idempotency_and_replay_conflict(self):
        identity = uuid4().hex
        line = self.line(qty=2)
        first = self.batch([line], sets=4, request_id=identity)
        self.assertEqual(first, self.batch([line], sets=4, request_id=identity))
        self.assertEqual(self.detail()[1]["available"], 92)
        self.batch([line], sets=5, request_id=identity, status=409)
        self.assertEqual(self.detail()[1]["available"], 92)

    def test_stale_inventory_is_rejected(self):
        saved = self.line(qty=2)
        self.post(self.admin, "/api/assets/operations", {
            "action": "receive", "item_id": self.part, "campus_id": self.campus,
            "quantity": 1, "reason": "竞争库存更新",
        })
        self.batch([saved], status=409)
        self.assertEqual(self.detail()[1]["available"], 101)

    def test_existing_quantity_inbound_and_authorized_new_material(self):
        self.batch([self.line(qty=20)], mode="inbound")
        self.assertEqual(self.detail()[1]["available"], 120)
        draft = {"item_id": None, "name": "BOM新元件", "specification": "0603",
                 "unit": "颗", "code": "C98765433", "quantity": 12}
        self.batch([draft], mode="inbound", status=403)
        self.assertEqual(self.batch([draft], mode="inbound", client=self.admin)["created"].__len__(), 1)
        candidates = self.preview("new.csv", "编号,名称,数量\nC98765433,BOM新元件,12\n".encode())["rows"][0]["candidates"]
        self.assertEqual(len(candidates), 1)

    def test_new_draft_repeated_rows_share_one_item(self):
        draft = {"item_id": None, "name": "BOM合成芯片", "specification": "SOP8",
                 "unit": "个", "code": "C98765435", "quantity": 4}
        other = {**draft, "quantity": 6}
        result = self.batch([draft, other], mode="inbound", client=self.admin)
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["operations"][0]["quantity"], 10)
        self.assertEqual(self.detail(result["created"][0])[1]["available"], 10)

    def test_unlinked_c_code_is_not_silently_matched_by_same_name(self):
        table = "嘉立创编号,名称,型号,数量\nC99999999,BOM测试电阻,10k 0603,2\n".encode()
        result = self.preview("bom.csv", table)
        self.assertEqual(result["rows"][0]["candidates"], [])

    def test_status_and_unit_material_rejected(self):
        status_id = self.post(self.admin, "/api/assets/catalog/items", {
            "name": "BOM测试螺丝", "unit": "袋", "kind": "consumable",
            "consumable_mode": "status",
        })["id"]
        unit_id = self.post(self.admin, "/api/assets/catalog/items", {
            "name": "BOM测试单件电机", "unit": "台", "kind": "returnable",
        })["id"]
        self.batch([self.line(status_id)], status=409)
        self.batch([self.line(unit_id)], mode="inbound", status=409)

    def test_unrecognized_columns_are_editable_after_raw_import(self):
        # Raw import must not demand recognizable BOM headers or choose an operation.
        content = ("开发板用料清单\n"
                   "Vendor Ref,Value Text,Pieces Per PCB,Measurement\n"
                   "C98765432,BOM测试电阻,7,颗\n").encode()
        response = self.member.post("/api/assets/bom/import", data={
            "file": (io.BytesIO(content), "custom.csv"),
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.get_json())
        parsed = response.get_json()
        self.assertTrue(parsed["ok"])
        self.assertGreaterEqual(len(parsed["lines"]), 3)
        self.assertEqual(parsed["lines"][1]["cells"][0], "Vendor Ref")
        self.assertEqual(parsed["lines"][2]["cells"][2], "7")
        # The editor can remap columns and correct the material before matching.
        matched = self.member.post("/api/assets/bom/match", json={
            "campus_id": self.campus, "rows": [{
                "code": "C98765432", "name": "BOM测试电阻",
                "specification": "10k 0603", "quantity": 7, "unit": "颗",
            }],
        })
        self.assertEqual(matched.status_code, 200, matched.get_json())
        self.assertEqual(matched.get_json()["matches"][0][0]["id"], self.part)
        self.assertEqual(self.detail()[1]["available"], 100)

    def test_match_validation_and_import_limit(self):
        result = self.member.post("/api/assets/bom/match", json={
            "campus_id": self.campus,
            "rows": [{"code": "", "name": "", "specification": "", "quantity": 1, "unit": "个"}],
        })
        self.assertEqual(result.status_code, 400, result.get_json())
        many = "Name,Qty\n" + "".join("Part"+str(i)+",1\n" for i in range(201))
        result = self.member.post("/api/assets/bom/import", data={
            "file": (io.BytesIO(many.encode()), "many.csv"),
        }, content_type="multipart/form-data")
        self.assertEqual(result.status_code, 400, result.get_json())
        anonymous = app_module.app.test_client()
        self.assertEqual(anonymous.post("/api/assets/bom/import").status_code, 401)
        self.assertEqual(anonymous.post("/api/assets/bom/match", json={}).status_code, 401)

    def test_table_workbench_nav(self):
        page = self.member.get("/assets?view=table")
        self.assertEqual(page.status_code, 200)
        for marker in (b"asset-bom-page", b"asset-bom-header-row", b"asset-bom-mapping",
                       b"asset-bom-rows", b"asset-bom-operation"):
            self.assertIn(marker, page.data)

    def match(self, data, status=200):
        response = self.member.post('/api/assets/bom/match', json=data)
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def test_bounded_large_upload_and_match(self):
        raw = 'Name,Qty\n' + ''.join('x' * 100 + str(i) + ',1\n' for i in range(190))
        response = self.member.post('/api/assets/bom/import', data={
            'file': (io.BytesIO(raw.encode()), 'large.csv')})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(len(response.get_json()['lines']), 191)
        self.match({
            'campus_id': self.campus, 'rows': [{'code': 'C98765432', 'name': 'x' * 90,
                'quantity': '1', 'unit': 'pcs'} for _ in range(180)]})
        response = self.member.post('/api/assets/catalog/items', json={'name': 'x' * 17000})
        self.assertEqual(response.status_code, 413)
        response = self.member.post('/api/assets/bom/match', json={'padding': 'x' * (512 * 1024)})
        self.assertEqual(response.status_code, 413)
        response = self.member.post('/api/assets/bom/import', data={
            'file': (io.BytesIO(b'x' * (2 * 1024 * 1024 + 1)), 'huge.csv')})
        self.assertEqual(response.status_code, 400)

    def test_overflow_rows_after_blanks_are_rejected_csv_and_xlsx(self):
        rows = [['Name', 'Qty']] + [[f'Part{i}', 1] for i in range(200)] + [[]] * 20 + [['tail', 5]]
        book = Workbook()
        for record in rows:
            book.active.append(record)
        output = io.BytesIO()
        book.save(output)
        csv_data = '\n'.join(','.join(map(str, record)) for record in rows).encode()
        for filename, content in [('overflow.csv', csv_data), ('overflow.xlsx', output.getvalue())]:
            with self.subTest(filename=filename):
                self.preview(filename, content, status=400)
                response = self.member.post('/api/assets/bom/import', data={
                    'file': (io.BytesIO(content), filename)})
                self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.detail()[1]['available'], 100)

    def test_quantities_are_validated_without_rounding(self):
        for value in ['1.00000000000000001', '999999999.99999999', '1,2', 'NaN', 'Infinity']:
            with self.subTest(value=value):
                self.match({
                    'campus_id': self.campus, 'rows': [{'code': 'C98765432', 'quantity': value}]}, status=400)
        for value in ['1', '1.0', '1e2', '1,000']:
            self.match({
                'campus_id': self.campus, 'rows': [{'code': 'C98765432', 'quantity': value}]})

    def test_material_id_overflow_and_long_csv_cell_are_bad_requests(self):
        self.match({
            'campus_id': self.campus, 'rows': [{'code': 'M:9223372036854775808', 'quantity': 1}]}, status=400)
        response = self.member.post('/api/assets/bom/import', data={
            'file': (io.BytesIO(('Name,Qty\n' + 'x' * 140000 + ',1').encode()), 'long.csv')})
        self.assertEqual(response.status_code, 400, response.get_json())

    def test_bad_files_and_unauthorized_access(self):
        self.preview("bom.xls", b"nonsense", status=400)
        self.preview("bom.csv", b"invalid,header\n1,2", status=400)
        self.preview("bom.csv", b"Name,Quantity\nPart,1.5", status=400)
        anonymous = app_module.app.test_client()
        self.assertEqual(anonymous.post("/api/assets/bom/preview").status_code, 401)
        self.assertEqual(anonymous.post("/api/assets/bom/commit", json={}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
