"""Cross-feature Flask HTTP contracts, including the authorization matrix."""
import unittest
from uuid import uuid4
from test_tasks import app_module


class TestFeatureContracts(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        response = self.client.post("/login", json={
            "username": "admin", "password": "ci-test-password-only"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_workspaces_and_security_panel_render(self):
        for path in ("/", "/tasks", "/manage", "/members", "/account",
                     "/funds/", "/invoices/", "/discussions", "/admin/security"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.content_type)

    def test_public_client_cannot_read_internal_api(self):
        anonymous = app_module.app.test_client()
        for path in ("/api/data", "/api/members", "/api/groups", "/api/projects",
                     "/api/account/profile", "/funds/api/records",
                     "/invoices/api/invoices", "/api/admin/permissions",
                     "/api/admin/audit"):
            with self.subTest(path=path):
                response = anonymous.get(path)
                self.assertIn(response.status_code, (401, 403, 302), path)

    def test_member_boundaries_across_workspaces(self):
        username = "unit_" + uuid4().hex[:12]
        created = self.client.post("/api/member", json={
            "name": "跨模块权限测试", "username": username, "role": "member",
            "password": "member-test-password"})
        self.assertTrue(created.get_json()["ok"], created.get_json())
        member = app_module.app.test_client()
        self.assertTrue(member.post("/login", json={
            "username": username, "password": "member-test-password",
        }).get_json()["ok"])
        for path in ("/api/data", "/api/account/profile",
                     "/api/groups", "/api/projects", "/invoices/api/stats"):
            with self.subTest(path=path):
                self.assertEqual(member.get(path).status_code, 200)
        for path in ("/funds/api/records", "/api/admin/permissions",
                     "/api/admin/audit", "/invoices/api/owners"):
            with self.subTest(path=path):
                self.assertEqual(member.get(path).status_code, 403)
        for path, payload in (
            ("/api/task", {"title": "未经授权"}),
            ("/api/groups", {"name": "新专业组"}),
            ("/api/projects", {"name": "新造车组别"}),
        ):
            with self.subTest(path=path):
                self.assertEqual(member.post(path, json=payload).status_code, 403)

    def test_ledger_filters_sort_export_and_delete_roundtrip(self):
        marker = "unit-" + uuid4().hex[:12]
        ids = []
        try:
            for amount in ("1.25", "2.75"):
                response = self.client.post("/funds/api/records", json={
                    "record_type": "进", "amount": amount,
                    "record_date": "2026-10-13", "source_dest": marker,
                })
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(response.get_json()["code"], 0)
                ids.append(response.get_json()["id"])
            result = self.client.get("/funds/api/records", query_string={
                "keyword": marker, "amount_min": "1.25",
                "amount_max": "2.75", "sort_by": "amount", "sort_order": "ASC"
            })
            self.assertEqual(result.status_code, 200)
            rows = result.get_json()["data"]
            self.assertEqual([r["id"] for r in rows], ids)
            self.assertEqual([r["amount"] for r in rows], ["1.25", "2.75"])
            self.assertEqual(self.client.get("/funds/api/stats").status_code, 200)
            csv_response = self.client.get("/funds/api/export", query_string={"keyword": marker})
            self.assertEqual(csv_response.status_code, 200)
            self.assertIn(marker, csv_response.get_data(as_text=True))
            for value in ("NaN", "1e1000"):
                self.assertEqual(self.client.get("/funds/api/records",
                    query_string={"amount_min": value}).status_code, 400)
        finally:
            for rid in ids:
                current = self.client.get("/funds/api/records/" + str(rid)).get_json()["data"]
                self.assertEqual(self.client.delete(
                    "/funds/api/records/" + str(rid),
                    query_string={"version": current["version"]}).status_code, 200)

    def test_invoice_validation_and_audit_contracts(self):
        for query in ("page=0", "per_page=101", "min_amount=-1",
                      "max_amount=Infinity", "submitter_id=-1"):
            with self.subTest(query=query):
                response = self.client.get("/invoices/api/invoices?" + query)
                self.assertEqual(response.status_code, 400)
        response = self.client.get("/api/admin/audit?before=nope")
        self.assertEqual(response.status_code, 400)
        response = self.client.get("/api/admin/permissions")
        self.assertEqual(response.status_code, 200)
        self.assertIn("members", response.get_json())


if __name__ == "__main__":
    unittest.main()
