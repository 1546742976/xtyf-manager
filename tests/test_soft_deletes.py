"""Reversible member and catalog deletion, permissions, and history safety."""
import unittest
from uuid import uuid4

from test_tasks import app_module


class TestSoftDeletes(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.assertTrue(self.admin.post("/login", json={
            "username": "admin", "password": "ci-test-password-only"
        }).get_json()["ok"])
        self.suffix = uuid4().hex[:12]
        self.username = "archive_" + self.suffix
        created = self.admin.post("/api/member", json={
            "name": "待删除成员", "username": self.username,
            "password": "archive-member-password"
        })
        self.assertEqual(created.status_code, 200, created.get_json())
        self.member_id = created.get_json()["id"]
        self.member = app_module.app.test_client()
        self.assertTrue(self.member.post("/login", json={
            "username": self.username, "password": "archive-member-password"
        }).get_json()["ok"])

    def _directory(self, show=False):
        url = "/api/members?include_deleted=1" if show else "/api/members"
        return self.admin.get(url).get_json()["members"]

    def test_member_delete_preserves_tasks_and_restoration_requires_enable(self):
        created_task = self.admin.post("/api/task", json={
            "title": "归档成员仍拥有此任务 " + self.suffix, "owner_id": self.member_id
        }).get_json()
        self.assertTrue(created_task["ok"])
        task_id = created_task["id"]
        self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/delete", json={
            "confirm_username": "wrong"
        }).status_code, 400)
        result = self.admin.post(f"/api/member/{self.member_id}/delete", json={
            "confirm_username": self.username
        })
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(self.member.get("/api/data").status_code, 401)
        self.assertEqual(self.admin.post("/login", json={
            "username": self.username, "password": "archive-member-password"
        }).status_code, 400)
        self.assertFalse(any(x["id"] == self.member_id for x in self._directory()))
        archived = next(x for x in self._directory(show=True) if x["id"] == self.member_id)
        self.assertTrue(archived["is_deleted"])
        self.assertEqual(archived["is_active"], 0)
        self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/toggle", json={}).status_code, 409)
        self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/profile", json={
            "username": self.username + "_new"
        }).status_code, 409)
        task = next(x for x in self.admin.get("/api/data").get_json()["tasks"] if x["id"] == task_id)
        self.assertEqual(task["owner_id"], self.member_id)
        self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/delete", json={
            "confirm_username": self.username
        }).status_code, 409)
        restored = self.admin.post(f"/api/member/{self.member_id}/restore", json={})
        self.assertEqual(restored.status_code, 200)
        record = next(x for x in self._directory() if x["id"] == self.member_id)
        self.assertFalse(record["is_deleted"])
        self.assertEqual(record["is_active"], 0)
        self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/toggle", json={}).status_code, 200)
        fresh = app_module.app.test_client()
        self.assertTrue(fresh.post("/login", json={
            "username": self.username, "password": "archive-member-password"
        }).get_json()["ok"])

    def test_member_delete_is_admin_only_and_self_safe(self):
        self.assertEqual(self.member.post(f"/api/member/{self.member_id}/delete", json={
            "confirm_username": self.username
        }).status_code, 403)
        self.assertEqual(self.member.post(f"/api/member/{self.member_id}/restore", json={}).status_code, 403)
        self.assertEqual(self.member.get("/api/members?include_deleted=1").status_code, 403)
        admin_id = self.admin.get("/api/data").get_json()["me"]["id"]
        self.assertEqual(self.admin.post(f"/api/member/{admin_id}/delete", json={
            "confirm_username": "admin"
        }).status_code, 403)
        self.assertEqual(self.admin.post("/api/member/999999999/delete", json={
            "confirm_username": "does-not-exist"
        }).status_code, 404)

    def test_two_group_dictionaries_retain_existing_relationships(self):
        professional = self.admin.post("/api/groups", json={
            "name": "归档专业_" + self.suffix
        }).get_json()["id"]
        vehicle = self.admin.post("/api/projects", json={
            "name": "归档车组_" + self.suffix
        }).get_json()["id"]
        self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/groups", json={
            "group_ids": [professional]
        }).status_code, 200)
        self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/projects", json={
            "project_ids": [vehicle]
        }).status_code, 200)
        task = self.admin.post("/api/task", json={
            "title": "历史造车归属 " + self.suffix,
            "owner_id": self.member_id, "project_id": vehicle
        }).get_json()["id"]
        for path, name in (("groups", "归档专业_" + self.suffix),
                           ("projects", "归档车组_" + self.suffix)):
            obj_id = professional if path == "groups" else vehicle
            self.assertEqual(self.member.post(f"/api/{path}/{obj_id}/delete", json={
                "confirm_name": name
            }).status_code, 403)
            self.assertEqual(self.admin.post(f"/api/{path}/{obj_id}/delete", json={
                "confirm_name": "wrong"
            }).status_code, 400)
            self.assertEqual(self.admin.post(f"/api/{path}/{obj_id}/delete", json={
                "confirm_name": name
            }).status_code, 200)
            entry = next(x for x in self.admin.get(f"/api/{path}").get_json()[path] if x["id"] == obj_id)
            self.assertEqual(entry["is_active"], 0)
            self.assertEqual(self.admin.post(f"/api/{path}/{obj_id}/delete", json={
                "confirm_name": name
            }).status_code, 409)
        self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/groups", json={
            "group_ids": [professional]
        }).status_code, 200)
        self.assertEqual(self.member.post("/api/account/profile", json={
            "group_ids": [professional], "project_ids": [vehicle]
        }).status_code, 200)
        self.assertEqual(self.admin.post("/api/member", json={
            "name": "非法归档分配", "username": "blocked_" + self.suffix,
            "password": "blocked-password", "group_ids": [professional]
        }).status_code, 400)
        self.assertEqual(self.admin.post("/api/task", json={
            "title": "非法归档车辆归属", "project_id": vehicle
        }).status_code, 400)
        data = self.admin.get("/api/data").get_json()
        historical = next(t for t in data["tasks"] if t["id"] == task)
        self.assertEqual(historical["project"], "归档车组_" + self.suffix)
        for path, obj_id in (("groups", professional), ("projects", vehicle)):
            self.assertEqual(self.admin.post(f"/api/{path}/{obj_id}/restore", json={}).status_code, 200)
            self.assertEqual(next(x for x in self.admin.get(f"/api/{path}").get_json()[path]
                                  if x["id"] == obj_id)["is_active"], 1)
            self.assertEqual(self.admin.post(f"/api/{path}/{obj_id}/restore", json={}).status_code, 409)

    def test_member_and_group_deletion_survive_repeated_migration(self):
        from modules.task_migrations import migrate
        from common import DB_PATH
        import sqlite3
        from contextlib import closing
        group = self.admin.post("/api/groups", json={
            "name": "幂等归档_" + self.suffix
        }).get_json()["id"]
        self.assertEqual(self.admin.post(f"/api/groups/{group}/delete", json={
            "confirm_name": "幂等归档_" + self.suffix
        }).status_code, 200)
        with closing(sqlite3.connect(DB_PATH)) as db, db:
            migrate(db)
            migrate(db)
            archived = db.execute("SELECT deleted_at FROM groups WHERE id=?", (group,)).fetchone()[0]
        self.assertIsNotNone(archived)

    def test_page_has_delete_and_restore_controls(self):
        html = self.admin.get("/members").get_data(as_text=True)
        self.assertIn("m-show-deleted", html)
        with self.admin.get("/static/manage-members.js") as response:
            script = response.get_data(as_text=True)
        for field in ("data-member-delete", "data-member-restore",
                      "data-delete-member-group", "data-restore-member-group",
                      "data-delete-member-project", "data-restore-member-project"):
            self.assertIn(field, script)


if __name__ == "__main__":
    unittest.main()
