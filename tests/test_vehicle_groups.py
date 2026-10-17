"""Vehicle-group membership is independent from professional specialization."""
import csv
import io
import unittest

from test_tasks import app_module
from modules.task_migrations import DEFAULT_VEHICLE_GROUPS


class TestVehicleGroupMembership(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.assertTrue(self.admin.post("/login", json={
            "username": "admin", "password": "123456",
        }).get_json()["ok"])

    def test_vehicle_catalog_default_and_separate_member_assignment(self):
        catalog = self.admin.get("/api/projects").get_json()
        self.assertTrue(catalog["ok"])
        self.assertTrue(set(DEFAULT_VEHICLE_GROUPS).issubset({x["name"] for x in catalog["projects"]}))
        specialized = self.admin.post("/api/groups", json={"name": "专项视觉回归"}).get_json()["id"]
        a = self.admin.post("/api/projects", json={"name": "测试步兵项目"}).get_json()["id"]
        b = self.admin.post("/api/projects", json={"name": "测试哨兵项目"}).get_json()["id"]
        created = self.admin.post("/api/member", json={
            "name": "跨车组开发者", "username": "vehicle_group_member_test",
            "password": "vehicle-group-password", "group_ids": [specialized],
            "project_ids": [a, b],
        }).get_json()
        self.assertTrue(created["ok"])
        member_id = created["id"]
        members = self.admin.get("/api/members").get_json()["members"]
        member = next(x for x in members if x["id"] == member_id)
        self.assertEqual(member["group_ids"], [specialized])
        self.assertEqual(set(member["project_ids"]), {a, b})
        # New task inherits no professional labels from its owner.
        task = self.admin.post("/api/task", json={
            "title": "哨兵视觉导航测试任务", "owner_id": member_id, "project_id": b,
        }).get_json()
        self.assertTrue(task["ok"])
        payload = self.admin.get("/api/data").get_json()
        actual = next(x for x in payload["tasks"] if x["id"] == task["id"])
        self.assertEqual(actual["group_ids"], [])
        self.assertEqual(actual["project_id"], b)
        self.assertEqual(actual["project"], "测试哨兵项目")
        updated = self.admin.post(f"/api/member/{member_id}/projects", json={"project_ids": [a]})
        self.assertEqual(updated.status_code, 200)
        member = next(x for x in self.admin.get("/api/members").get_json()["members"] if x["id"] == member_id)
        self.assertEqual(member["project_ids"], [a])
        self.assertEqual(member["group_ids"], [specialized])
        # Explicit membership edit must not rewrite previously assigned tasks.
        current_task = next(x for x in self.admin.get("/api/data").get_json()["tasks"] if x["id"] == task["id"])
        self.assertEqual(current_task["project_id"], b)

    def test_vehicle_group_rename_updates_task_and_member_exports(self):
        unit = self.admin.post("/api/projects", json={"name": "测试飞镖原名"}).get_json()["id"]
        member = self.admin.post("/api/member", json={
            "name": "飞镖回归成员", "username": "vehicle_export_member",
            "password": "vehicle-export-password", "project_ids": [unit],
        }).get_json()
        task = self.admin.post("/api/task", json={
            "title": "飞镖轨迹", "owner_id": member["id"], "project_id": unit,
        }).get_json()
        self.assertEqual(self.admin.post(f"/api/projects/{unit}", json={"name": "测试飞镖新名"}).status_code, 200)
        data = self.admin.get("/api/data").get_json()
        current = next(x for x in data["tasks"] if x["id"] == task["id"])
        person = next(x for x in data["all_members"] if x["id"] == member["id"])
        self.assertEqual(current["project"], "测试飞镖新名")
        self.assertIn("测试飞镖新名", person["project_names"])
        member_rows = list(csv.DictReader(io.StringIO(
            self.admin.get("/api/members/export.csv").get_data(as_text=True).lstrip("\ufeff")
        )))
        row = next(x for x in member_rows if x["ID"] == str(member["id"]))
        self.assertEqual(row["造车组别"], "测试飞镖新名")
        task_rows = list(csv.DictReader(io.StringIO(
            self.admin.get("/export.csv").get_data(as_text=True).lstrip("\ufeff")
        )))
        self.assertEqual(next(x for x in task_rows if x["ID"] == str(task["id"]))["组别"], "测试飞镖新名")

    def test_invalid_vehicle_links_and_member_permission(self):
        created = self.admin.post("/api/member", json={
            "name": "车组限制成员", "username": "vehicle_permission_member",
            "password": "vehicle-permission-password",
        }).get_json()
        member_id = created["id"]
        for payload in ({"project_ids": [9999999]}, {"project_ids": [True]}, {"project_ids": [1, 1]}):
            self.assertEqual(self.admin.post(f"/api/member/{member_id}/projects", json=payload).status_code, 400)
        member = app_module.app.test_client()
        self.assertTrue(member.post("/login", json={
            "username": "vehicle_permission_member", "password": "vehicle-permission-password",
        }).get_json()["ok"])
        self.assertEqual(member.post(f"/api/member/{member_id}/projects", json={"project_ids": []}).status_code, 403)
        self.assertEqual(member.post("/api/projects", json={"name": "越权车辆组"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
