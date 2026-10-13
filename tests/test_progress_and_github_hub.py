"""Progress history, multi-parent graph integrity and embedded GitHub gateway contracts."""
import io
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image
from test_tasks import app_module
from modules.discussions import GitHubError, _atom_items


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
    ATOM = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>公开主题 &amp; 建议</title>
        <updated>2026-10-13T00:00:00Z</updated>
        <link rel="alternate" href="https://github.com/SHM-white/xtyf-manager/discussions/42" />
        <author><name>someone</name></author>
        <content type="html">&lt;p&gt;欢迎&lt;strong&gt;测试&lt;/strong&gt;&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;</content>
      </entry>
      <entry>
        <title>伪造仓库的主题</title>
        <link rel="alternate" href="https://github.com/untrusted/repo/discussions/88" />
      </entry>
    </feed>""".encode("utf-8")

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
        app_module.app.config["GITHUB_WRITE_ENABLED"] = "1"  # Legacy setting must not enable writing.

    def tearDown(self):
        for key, value in (("GITHUB_TOKEN", self.prev_token),
                           ("GITHUB_WRITE_ENABLED", self.prev_write),
                           ("GITHUB_REPOSITORY", self.prev_repo)):
            if value is None:
                app_module.app.config.pop(key, None)
            else:
                app_module.app.config[key] = value

    def test_issues_public_read_and_external_only_ui(self):
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
        self.assertEqual(response.get_json()["source"], "rest")
        mocked.assert_called_once()
        self.assertIn("/issues?state=all", mocked.call_args.args[0])
        page = self.client.get("/discussions/").get_data(as_text=True)
        self.assertIn('id="github-new"', page)
        self.assertIn("https://github.com/SHM-white/xtyf-manager/issues/new/choose", page)
        self.assertIn("https://github.com/SHM-white/xtyf-manager/discussions/new/choose", page)
        self.assertNotIn("github-compose", page)
        # The old POST endpoint must remain unavailable even if an operator
        # has accidentally left GITHUB_WRITE_ENABLED=1 in the environment.
        self.assertEqual(self.client.post("/discussions/api/post", json={
            "kind": "issues", "action": "create", "title": "x", "body": "x",
        }).status_code, 404)

    def test_public_discussions_atom_without_token(self):
        parsed = _atom_items(self.ATOM, "SHM-white/xtyf-manager")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["number"], 42)
        self.assertEqual(parsed[0]["title"], "公开主题 & 建议")
        self.assertIn("欢迎测试", parsed[0]["body"])
        self.assertNotIn("<script>", parsed[0]["body"])
        self.assertIsNone(parsed[0]["comments_count"])
        with patch("modules.discussions._atom_feed", return_value=parsed) as mocked:
            listed = self.client.get("/discussions/api/items?kind=discussions")
            self.assertEqual(listed.status_code, 200, listed.get_json())
            self.assertEqual(listed.get_json()["source"], "atom")
            self.assertFalse(listed.get_json()["comments_available"])
            detail = self.client.get("/discussions/api/item/discussions/42")
            self.assertEqual(detail.status_code, 200, detail.get_json())
            self.assertEqual(detail.get_json()["item"]["body"], parsed[0]["body"])
            self.assertEqual(detail.get_json()["comments"], [])
            self.assertFalse(detail.get_json()["comments_available"])
            self.assertEqual(mocked.call_count, 2)

    def test_graphql_read_only_and_fallback_to_atom(self):
        app_module.app.config["GITHUB_TOKEN"] = "read-only-token"
        discussion = {
            "number": 42, "title": "GraphQL 全文", "bodyText": "全文",
            "url": "https://github.com/SHM-white/xtyf-manager/discussions/42",
            "author": {"login": "someone"}, "comments": {"totalCount": 1},
        }
        gql_data = {"repository": {"discussions": {"nodes": [discussion]}}}
        with patch("modules.discussions._graphql", return_value=gql_data):
            response = self.client.get("/discussions/api/items?kind=discussions")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["source"], "graphql")
        self.assertTrue(response.get_json()["comments_available"])
        with patch("modules.discussions._graphql", side_effect=GitHubError("bad token")), \
             patch("modules.discussions._atom_feed", return_value=_atom_items(self.ATOM, "SHM-white/xtyf-manager")):
            response = self.client.get("/discussions/api/items?kind=discussions")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["source"], "atom")

    def test_unavailable_and_invalid_atom_are_not_silently_accepted(self):
        with self.assertRaises(GitHubError):
            _atom_items(b"<html/>", "SHM-white/xtyf-manager")
        with self.assertRaises(GitHubError):
            _atom_items(b"<feed>", "SHM-white/xtyf-manager")
        with patch("modules.discussions._atom_feed", side_effect=GitHubError("feed offline")):
            response = self.client.get("/discussions/api/items?kind=discussions")
        self.assertEqual(response.status_code, 502, response.get_json())
        self.assertFalse(response.get_json()["ok"])
        anonymous = app_module.app.test_client()
        self.assertEqual(anonymous.get("/discussions/api/items").status_code, 401)


if __name__ == "__main__":
    unittest.main()
