"""Member profile editing, immutable IDs, permissions and session behavior."""
import unittest
from uuid import uuid4

from test_tasks import app_module


class TestMemberProfiles(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.assertTrue(self.admin.post("/login", json={
            "username": "admin", "password": "ci-test-password-only"
        }).get_json()["ok"])
        self.username = "profile_" + uuid4().hex[:12]
        self.password = "profile-member-password"
        res = self.admin.post("/api/member", json={
            "name": "初始姓名", "username": self.username, "password": self.password,
        })
        self.assertEqual(res.status_code, 200)
        self.member_id = res.get_json()["id"]

    def member_client(self, username=None, password=None):
        client = app_module.app.test_client()
        login = client.post("/login", json={
            "username": username or self.username, "password": password or self.password,
        })
        self.assertEqual(login.status_code, 200, login.get_json())
        return client

    def test_admin_complete_edit_keeps_id_and_task_foreign_key(self):
        group = self.admin.post("/api/groups", json={"name": "专业_" + self.username}).get_json()["id"]
        project = self.admin.post("/api/projects", json={"name": "车组_" + self.username}).get_json()["id"]
        task = self.admin.post("/api/task", json={
            "title": "成员标识不变", "owner_id": self.member_id, "project_id": project
        }).get_json()["id"]
        old_session = self.member_client()
        new_username = self.username + "_new"
        response = self.admin.post(f"/api/member/{self.member_id}/profile", json={
            "name": "管理员统一姓名", "username": new_username, "role": "manager",
            "is_active": True, "group_ids": [group], "project_ids": [project],
            "new_password": "new-admin-reset-pass",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        saved = response.get_json()["profile"]
        self.assertEqual(saved["id"], self.member_id)
        self.assertEqual(saved["name"], "管理员统一姓名")
        self.assertEqual(saved["username"], new_username)
        self.assertEqual(saved["role"], "manager")
        self.assertEqual(saved["group_ids"], [group])
        self.assertEqual(saved["project_ids"], [project])
        self.assertNotIn("password_hash", saved)
        self.assertEqual(old_session.get("/api/data").status_code, 401)
        self.assertEqual(self.admin.post("/login", json={
            "username": self.username, "password": self.password
        }).status_code, 400)
        updated_session = self.member_client(new_username, "new-admin-reset-pass")
        self.assertEqual(updated_session.get("/api/account/profile").get_json()["profile"]["id"], self.member_id)
        tasks = self.admin.get("/api/data").get_json()["tasks"]
        self.assertEqual(next(x for x in tasks if x["id"] == task)["owner_id"], self.member_id)

    def test_self_service_keeps_current_session_but_invalidates_other_sessions(self):
        first = self.member_client()
        second = self.member_client()
        group = self.admin.post("/api/groups", json={"name": "自助专业_" + self.username}).get_json()["id"]
        project = self.admin.post("/api/projects", json={"name": "自助车组_" + self.username}).get_json()["id"]
        response = first.post("/api/account/profile", json={
            "username": self.username + "_self",
            "group_ids": [group], "project_ids": [project],
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(first.get("/api/data").status_code, 200)
        self.assertEqual(second.get("/api/data").status_code, 401)
        self.assertEqual(first.get("/api/account/profile").get_json()["profile"]["name"], "初始姓名")
        self.assertEqual(first.get("/api/account/profile").get_json()["profile"]["project_ids"], [project])
        self.assertEqual(self.member_client(self.username + "_self").get("/account").status_code, 200)
        self.assertEqual(first.post("/api/password", json={
            "old": self.password, "new": "fresh-member-password"
        }).status_code, 200)

    def test_permissions_and_mass_assignment_are_rejected(self):
        client = self.member_client()
        for data in (
            {"name": "越权改姓名"}, {"role": "admin"}, {"is_active": False},
            {"id": 999}, {"session_version": 12}, {"new_password": "stolen-pass"},
        ):
            with self.subTest(data=data):
                self.assertEqual(client.post("/api/account/profile", json=data).status_code, 400)
        self.assertEqual(client.post(f"/api/member/{self.member_id}/profile", json={
            "name": "越权"
        }).status_code, 403)
        self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/profile", json={
            "password_hash": "plain"
        }).status_code, 400)
        self.assertEqual(client.get("/api/members").status_code, 403)
        self.assertEqual(client.get("/api/account/profile").status_code, 200)

    def test_validation_failure_is_atomic_and_duplicates_conflict(self):
        before = self.admin.get("/api/members").get_json()["members"]
        original = next(x for x in before if x["id"] == self.member_id)
        bad = self.admin.post(f"/api/member/{self.member_id}/profile", json={
            "name": "意外被写入", "group_ids": [999999999],
        })
        self.assertEqual(bad.status_code, 400)
        after = next(x for x in self.admin.get("/api/members").get_json()["members"] if x["id"] == self.member_id)
        self.assertEqual(after["name"], original["name"])
        self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/profile", json={
            "username": "admin"
        }).status_code, 409)
        for payload in ({"username": ""}, {"username": "a\nb"}, {"is_active": "false"},
                        {"role": "root"}, {"new_password": "short"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/profile", json=payload).status_code, 400)
        self.assertEqual(self.admin.post("/api/member/999999999/profile", json={
            "name": "none"
        }).status_code, 404)

    def test_account_security_and_admin_self_protection(self):
        self.assertEqual(self.admin.post("/api/account/profile", json={
            "name": "伪造管理员姓名"
        }).status_code, 400)
        own_id = self.admin.get("/api/account/profile").get_json()["profile"]["id"]
        self.assertEqual(self.admin.post(f"/api/member/{own_id}/profile", json={
            "role": "member"
        }).status_code, 400)
        self.assertEqual(self.admin.post(f"/api/member/{own_id}/profile", json={
            "is_active": False
        }).status_code, 400)
        self.assertEqual(self.admin.post(f"/api/member/{own_id}/profile", json={
            "new_password": "bypass-own-old-password"
        }).status_code, 400)
        self.assertEqual(self.admin.get("/api/account/profile").status_code, 200)
        disabled = self.admin.post(f"/api/member/{self.member_id}/profile", json={
            "is_active": False
        })
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(self.admin.post(f"/api/member/{self.member_id}/profile", json={
            "is_active": True
        }).status_code, 200)

    def test_manager_may_edit_self_but_not_other_profiles(self):
        manager_name = "manager_" + uuid4().hex[:12]
        created = self.admin.post("/api/member", json={
            "name": "项管测试", "username": manager_name,
            "password": "manager-profile-password", "role": "manager",
        }).get_json()
        self.assertTrue(created["ok"])
        manager = app_module.app.test_client()
        self.assertTrue(manager.post("/login", json={
            "username": manager_name, "password": "manager-profile-password"
        }).get_json()["ok"])
        self.assertEqual(manager.post(f"/api/member/{self.member_id}/profile", json={
            "name": "项管越权改名"
        }).status_code, 403)
        self.assertEqual(manager.post("/api/account/profile", json={
            "username": manager_name + "_edited"
        }).status_code, 200)
        self.assertEqual(manager.get("/api/account/profile").get_json()["profile"]["id"], created["id"])
        self.assertEqual(manager.post("/api/account/profile", json={
            "role": "admin"
        }).status_code, 400)

    def test_member_profile_ui(self):
        account = self.member_client().get("/account").get_data(as_text=True)
        self.assertIn("account-profile-form", account)
        self.assertIn("account-group-choices", account)
        self.assertIn("account-project-choices", account)
        management = self.admin.get("/members").get_data(as_text=True)
        self.assertIn("member-table", management)


if __name__ == "__main__":
    unittest.main()
