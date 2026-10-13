"""Progress history, multi-parent graph integrity and embedded GitHub gateway contracts."""
import io
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image
from test_tasks import app_module


class TaskProgressTests(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.assertTrue(self.admin.post("/login", json={
            "username": "admin", "password": "ci-test-password-only",
        }).get_json()["ok"])

    def new_task(self, **fields):
        response = self.admin.post("/api/task", json={"title": uuid4().hex, **fields})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def image(self):
        buf = io.BytesIO()
        Image.new("RGB", (16, 12), "blue").save(buf, format="PNG")
        buf.seek(0)
        return buf

    def test_progress_note_image_history_and_conflict(self):
        task = self.new_task()
        initial = self.admin.get(f"/api/task/{task['id']}/progress").get_json()
        self.assertEqual(initial["updates"], [])
        response = self.admin.post(f"/api/task/{task['id']}/progress", data={
            "progress": "35", "version": str(task["version"]),
            "note": "完成控制板焊接", "files": (self.image(), "result.png"),
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["version"], task["version"] + 1)
        history = self.admin.get(f"/api/task/{task['id']}/progress").get_json()["updates"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["progress"], 35)
        self.assertEqual(history[0]["note"], "完成控制板焊接")
        self.assertEqual(len(history[0]["images"]), 1)
        attachment = self.admin.get(history[0]["images"][0]["url"])
        self.assertEqual(attachment.status_code, 200)
        payload = self.admin.get("/api/data").get_json()
        item = next(item for item in payload["tasks"] if item["id"] == task["id"])
        self.assertEqual(item["progress"], 35)
        self.assertEqual(item["latest_progress"]["note"], "完成控制板焊接")
        self.assertEqual(len(item["latest_progress"]["image_ids"]), 1)
        stale = self.admin.post(f"/api/task/{task['id']}/progress", data={
            "progress": "80", "version": str(task["version"]),
        })
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(len(self.admin.get(f"/api/task/{task['id']}/progress").get_json()["updates"]), 1)

    def test_progress_validations_and_unchanged_note(self):
        task = self.new_task()
        url = f"/api/task/{task['id']}/progress"
        for value in ("-1", "101", "3.2", "", "nan"):
            with self.subTest(value=value):
                self.assertEqual(self.admin.post(url, data={
                    "progress": value, "version": str(task["version"]),
                }).status_code, 400)
        self.assertEqual(self.admin.post(url, data={
            "progress": "0", "version": str(task["version"]), "note": "x" * 2001,
        }).status_code, 400)
        self.assertEqual(self.admin.post(url, data={
            "progress": "0", "version": str(task["version"]),
        }).status_code, 400)
        self.assertEqual(self.admin.post(url, data={
            "progress": "0", "version": str(task["version"]), "note": "完成初步设计",
        }).status_code, 200)

    def test_progress_without_screenshot_and_empty_multipart_placeholder(self):
        # A real browser may submit files=(empty filename, empty payload) even
        # when no screenshot was selected. Both calls must save the progress.
        task = self.new_task()
        url = f"/api/task/{task['id']}/progress"
        text_only = self.admin.post(url, data={
            "progress": "25", "version": str(task["version"]),
            "note": "只记录工作进展，不附截图",
        }, content_type="multipart/form-data")
        self.assertEqual(text_only.status_code, 200, text_only.get_json())
        new_version = text_only.get_json()["version"]

        browser_empty_file = self.admin.post(url, data={
            "progress": "45", "version": str(new_version),
            "note": "", "files": (io.BytesIO(b""), ""),
        }, content_type="multipart/form-data")
        self.assertEqual(browser_empty_file.status_code, 200, browser_empty_file.get_json())

        # A note-only update must also work with no screenshots selected.
        note_only = self.admin.post(url, data={
            "progress": "45", "version": str(browser_empty_file.get_json()["version"]),
            "note": "只补充本次工作说明", "files": (io.BytesIO(b""), ""),
        }, content_type="multipart/form-data")
        self.assertEqual(note_only.status_code, 200, note_only.get_json())

        history = self.admin.get(url).get_json()["updates"]
        self.assertEqual([item["progress"] for item in history[:3]], [45, 45, 25])
        self.assertEqual(history[0]["note"], "只补充本次工作说明")
        self.assertEqual(history[0]["images"], [])
        self.assertEqual(history[1]["images"], [])
        current = next(item for item in self.admin.get("/api/data").get_json()["tasks"]
                       if item["id"] == task["id"])
        self.assertEqual(current["progress"], 45)
        self.assertEqual(current["version"], new_version + 2)

    def test_unauthorized_member_cannot_update_progress(self):
        task = self.new_task()
        username = "progress_" + uuid4().hex[:12]
        self.assertEqual(self.admin.post("/api/member", json={
            "name": "无关成员", "username": username, "password": "member-test-password",
        }).status_code, 200)
        member = app_module.app.test_client()
        self.assertTrue(member.post("/login", json={
            "username": username, "password": "member-test-password",
        }).get_json()["ok"])
        response = member.post(f"/api/task/{task['id']}/progress", data={
            "progress": "20", "version": str(task["version"]), "note": "unauthorized",
        })
        self.assertEqual(response.status_code, 403)

    def test_mixed_edge_cycle_is_rejected(self):
        first = self.new_task()
        second = self.new_task(parent_id=first["id"])
        third = self.new_task(predecessor_ids=[second["id"]])
        response = self.admin.post(f"/api/task/{first['id']}", json={
            "version": first["version"], "parent_id": third["id"],
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("循环", response.get_json()["error"])


class GitHubGatewayTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.assertTrue(self.client.post("/login", json={
            "username": "admin", "password": "ci-test-password-only",
        }).get_json()["ok"])
        self.prev_token = app_module.app.config.get("GITHUB_TOKEN")
        self.prev_write = app_module.app.config.get("GITHUB_WRITE_ENABLED")
        self.prev_repo = app_module.app.config.get("GITHUB_REPOSITORY")
        app_module.app.config["GITHUB_REPOSITORY"] = "SHM-white/xtyf-manager"
        app_module.app.config["GITHUB_TOKEN"] = ""
        app_module.app.config["GITHUB_WRITE_ENABLED"] = "0"

    def tearDown(self):
        for key, value in (("GITHUB_TOKEN", self.prev_token),
                           ("GITHUB_WRITE_ENABLED", self.prev_write),
                           ("GITHUB_REPOSITORY", self.prev_repo)):
            if value is None:
                app_module.app.config.pop(key, None)
            else:
                app_module.app.config[key] = value

    def test_issues_read_inside_workspace_with_no_token(self):
        fixture = [{
            "number": 17, "title": "<b>标题</b>", "body": "问题复现",
            "user": {"login": "robot"}, "comments": 2,
            "updated_at": "2026-10-13T00:00:00Z",
            "html_url": "https://github.com/SHM-white/xtyf-manager/issues/17",
        }]
        with patch("modules.discussions._remote", return_value=fixture) as mocked:
            response = self.client.get("/discussions/api/items?kind=issues")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["items"][0]["title"], "<b>标题</b>")
        mocked.assert_called_once()
        self.assertEqual(self.client.get("/discussions/api/items?kind=discussions").status_code, 503)
        self.assertEqual(self.client.post("/discussions/api/post", json={
            "kind": "issues", "action": "create", "title": "x", "body": "x",
        }).status_code, 403)

    def test_auth_and_post_validation_with_server_token(self):
        anonymous = app_module.app.test_client()
        self.assertEqual(anonymous.get("/discussions/api/items").status_code, 401)
        app_module.app.config["GITHUB_TOKEN"] = "ci-test-token"
        app_module.app.config["GITHUB_WRITE_ENABLED"] = "1"
        self.assertEqual(self.client.post("/discussions/api/post", json={
            "kind": "issues", "action": "create", "title": "", "body": "x",
        }).status_code, 400)
        def remote(path, payload=None, graphql=False):
            self.assertEqual(path, "/issues")
            self.assertIn("由巡天御风管理系统成员", payload["body"])
            return {"number": 36}
        with patch("modules.discussions._remote", side_effect=remote):
            result = self.client.post("/discussions/api/post", json={
                "kind": "issues", "action": "create",
                "title": "功能建议", "body": "新增功能",
            })
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()["number"], 36)


if __name__ == "__main__":
    unittest.main()
