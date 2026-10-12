"""使用临时 SQLite 数据文件的 Flask API 最小回归测试。"""
import os
import tempfile
import unittest

_TEMP = tempfile.TemporaryDirectory()
os.environ["PROGRESS_DB"] = os.path.join(_TEMP.name, "data.db")
os.environ["FUNDS_DIR"] = os.path.join(_TEMP.name, "funds")
os.environ["INVOICES_DIR"] = os.path.join(_TEMP.name, "invoices")
os.environ["PROGRESS_INITIAL_ADMIN_PASSWORD"] = "ci-test-password-only"

import app as app_module  # noqa: E402


class TestManagementAPI(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        login = self.client.post("/login", json={
            "username": "admin", "password": "ci-test-password-only",
        })
        self.assertTrue(login.get_json()["ok"])

    def test_task_create_and_query(self):
        response = self.client.post("/api/task", json={
            "title": "回归任务", "due_date": "2026-12-01",
        })
        self.assertEqual(response.status_code, 200)
        task_id = response.get_json()["id"]
        data = self.client.get("/api/data").get_json()
        self.assertTrue(any(t["id"] == task_id for t in data["tasks"]))

    def test_reject_invalid_task_dates(self):
        response = self.client.post("/api/task", json={
            "title": "日期错误", "start_date": "2026-12-05",
            "due_date": "2026-12-01",
        })
        self.assertEqual(response.status_code, 400)

    def test_reject_inactive_or_missing_owner(self):
        response = self.client.post("/api/task", json={
            "title": "负责人错误", "owner_id": 999999,
        })
        self.assertEqual(response.status_code, 400)

    def test_reject_invalid_task_update(self):
        created = self.client.post("/api/task", json={
            "title": "更新测试", "start_date": "2026-10-01", "due_date": "2026-10-02",
        }).get_json()["id"]
        invalid = self.client.post(f"/api/task/{created}", json={
            "start_date": "2026-10-03",
        })
        self.assertEqual(invalid.status_code, 400)

    def test_invoice_page_from_template(self):
        response = self.client.get("/invoices/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("巡天御风管理系统", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
