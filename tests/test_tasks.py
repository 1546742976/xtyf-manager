"""使用临时 SQLite 数据文件的 Flask API 最小回归测试。"""
import os
import tempfile
import unittest
import atexit
from unittest.mock import patch

_TEMP = tempfile.TemporaryDirectory()
os.environ["PROGRESS_DB"] = os.path.join(_TEMP.name, "data.db")
os.environ["FUNDS_DIR"] = os.path.join(_TEMP.name, "funds")
os.environ["INVOICES_DIR"] = os.path.join(_TEMP.name, "invoices")
os.environ["TASK_ATTACHMENTS_DIR"] = os.path.join(_TEMP.name, "task-attachments")
os.environ["PROGRESS_SECRET_PATH"] = os.path.join(_TEMP.name, "secret.key")
os.environ["PROGRESS_INITIAL_ADMIN_PASSWORD"] = "ci-test-password-only"

import app as app_module  # noqa: E402
# Queue recovery tests claim jobs explicitly against their own patched DB paths.
# A daemon started by an unrelated page request must not consume those fixtures
# or mutate another test's invoice directory while DB_PATH is temporarily patched.
_WORKER_PATCH = patch('modules.invoices.start_invoice_worker')
_WORKER_PATCH.start()
atexit.register(_WORKER_PATCH.stop)
from flask.testing import FlaskClient
from werkzeug.datastructures import Headers


class SecurityAwareTestClient(FlaskClient):
    """Exercise real CSRF + first-login password change in legacy API tests.

    Tests intentionally keep the same password to avoid reworking unrelated
    fixtures: change to a temporary password, then rotate back via the public
    authenticated API. The application security checks are never bypassed.
    """

    auto_onboard = True

    def open(self, *args, **kwargs):
        path = args[0] if args else kwargs.get("path", "")
        method = str(kwargs.get("method", "GET")).upper()
        if method not in {"GET", "HEAD", "OPTIONS"}:
            headers = Headers(kwargs.get("headers"))
            if "X-CSRF-Token" not in headers:
                token_response = super().open("/api/csrf", method="GET")
                token = token_response.get_json()["csrf_token"]
                headers["X-CSRF-Token"] = token
            kwargs["headers"] = headers
        response = super().open(*args, **kwargs)
        if (self.auto_onboard and path == "/login" and method == "POST"
                and response.is_json and response.get_json().get("ok")
                and response.get_json().get("must_change_password")):
            password = (kwargs.get("json") or {}).get("password")
            if password is None:
                raise AssertionError("test login should pass JSON credentials")
            temporary = password + "-temporary-rotation"
            first = self.post("/api/password", json={"old": password, "new": temporary})
            second = self.post("/api/password", json={"old": temporary, "new": password})
            if not (first.get_json().get("ok") and second.get_json().get("ok")):
                raise AssertionError("test fixture failed first-login password rotation")
        return response


app_module.app.test_client_class = SecurityAwareTestClient


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
