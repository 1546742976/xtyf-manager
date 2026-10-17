"""Configurable catalog and multi-group membership regression tests."""
import csv
import io
import unittest

from test_tasks import app_module
from modules.task_migrations import DEFAULT_GROUPS


class TestGroupCatalog(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        response = self.admin.post("/login", json={"username": "admin", "password": "123456"})
        self.assertTrue(response.get_json()["ok"])

    def test_default_groups_and_member_page_entry(self):
        data = self.admin.get("/api/groups").get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(set(DEFAULT_GROUPS).issubset({g["name"] for g in data["groups"]}))
        html = self.admin.get("/members").get_data(as_text=True)
        self.assertIn("member-group-catalog", html)
        self.assertIn("member-group-create-form", html)

    def test_multi_group_members_and_tasks_survive_rename_and_export(self):
        a = self.admin.post("/api/groups", json={"name": "专组回归甲"}).get_json()
        b = self.admin.post("/api/groups", json={"name": "专组回归乙"}).get_json()
        self.assertTrue(a["ok"] and b["ok"])
        ids = [a["id"], b["id"]]
        member = self.admin.post("/api/member", json={
            "name": "多组回归成员", "username": "multi_group_regression",
            "password": "group-regression-password", "group_ids": ids,
        }).get_json()
        self.assertTrue(member["ok"])
        task = self.admin.post("/api/task", json={
            "title": "多组归属回归", "owner_id": member["id"], "group_ids": ids,
        }).get_json()
        self.assertTrue(task["ok"])
        renamed = self.admin.post(f"/api/groups/{ids[0]}", json={"name": "专组重命名甲"})
        self.assertEqual(renamed.status_code, 200)
        data = self.admin.get("/api/data").get_json()
        m = next(x for x in data["all_members"] if x["id"] == member["id"])
        t = next(x for x in data["tasks"] if x["id"] == task["id"])
        self.assertEqual(set(m["group_ids"]), set(ids))
        self.assertEqual(set(t["group_ids"]), set(ids))
        expected = {"专组重命名甲", "专组回归乙"}
        self.assertEqual(set(m["group_name"].split(";")), expected)
        self.assertEqual(set(t["group_name"].split(";")), expected)
        rows = list(csv.DictReader(io.StringIO(
            self.admin.get("/api/members/export.csv").get_data(as_text=True).lstrip("\ufeff")
        )))
        self.assertEqual(set(next(x for x in rows if x["ID"] == str(member["id"]))["专业组"].split(";")), expected)
        self.assertEqual(self.admin.post("/api/groups", json={"name": "专组重命名甲"}).status_code, 409)
        self.assertEqual(self.admin.post("/api/groups", json={"name": "不允许;复合"}).status_code, 400)

    def test_member_cannot_modify_catalog(self):
        created = self.admin.post("/api/member", json={
            "name": "组别普通成员", "username": "group_catalog_regular",
            "password": "group-regular-password", "group_ids": [],
        }).get_json()
        self.assertTrue(created["ok"])
        client = app_module.app.test_client()
        self.assertTrue(client.post("/login", json={
            "username": "group_catalog_regular", "password": "group-regular-password",
        }).get_json()["ok"])
        self.assertEqual(client.get("/api/groups").status_code, 200)
        self.assertEqual(client.post("/api/groups", json={"name": "越权组"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
