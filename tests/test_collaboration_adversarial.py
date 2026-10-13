import threading
import unittest
from queue import Queue
from typing import TypedDict
from uuid import uuid4

from test_tasks import app_module


class TaskPayload(TypedDict, total=False):
    id: int
    version: int
    title: str
    detail: str


class TestCollaborationAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.admin = app_module.app.test_client()
        result = self.admin.post("/login", json={
            "username": "admin", "password": "ci-test-password-only",
        })
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.get_json()["ok"])

    def _member(self, label: str):
        suffix = f"{label}_{uuid4().hex[:8]}"
        result = self.admin.post("/api/member", json={
            "name": f"QA {suffix}", "username": suffix,
            "password": "qa-password", "group_ids": [],
        })
        self.assertEqual(result.status_code, 200)
        member_id = result.get_json()["id"]
        client = app_module.app.test_client()
        login = client.post("/login", json={
            "username": suffix, "password": "qa-password",
        })
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.get_json()["ok"])
        return member_id, client

    def _task(self, **fields) -> TaskPayload:
        result = self.admin.post("/api/task", json={"title": "QA task", **fields})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        return result.get_json()

    def test_membership_pivot_cannot_authorize_same_request(self) -> None:
        owner_id, _ = self._member("owner")
        outsider_id, outsider = self._member("outsider")
        task = self._task(owner_id=owner_id, participant_ids=[owner_id])
        response = outsider.post(f"/api/task/{task['id']}", json={
            "version": task["version"], "title": "unauthorized",
            "participant_ids": [outsider_id],
        })
        self.assertEqual(response.status_code, 403)
        current = next(item for item in self.admin.get("/api/data").get_json()["tasks"]
                       if item["id"] == task["id"])
        self.assertEqual(current["title"], "QA task"); self.assertEqual(current["participant_ids"], [owner_id])

    def test_same_version_concurrent_updates_have_one_winner(self) -> None:
        task = self._task()
        clients = [app_module.app.test_client(), app_module.app.test_client()]
        for client in clients:
            login = client.post("/login", json={
                "username": "admin", "password": "ci-test-password-only",
            })
            self.assertEqual(login.status_code, 200)
        gate = threading.Barrier(2)
        results: Queue[tuple[int, str]] = Queue()

        def submit(client, title: str) -> None:
            gate.wait()
            response = client.post(f"/api/task/{task['id']}", json={
                "version": task["version"], "title": title,
            })
            results.put((response.status_code, response.get_data(as_text=True)))

        threads = [threading.Thread(target=submit, args=(client, f"winner-{index}"))
                   for index, client in enumerate(clients)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        observed = [results.get_nowait() for _ in range(2)]
        self.assertEqual(sorted(status for status, _ in observed), [200, 409])
        current = next(item for item in self.admin.get("/api/data").get_json()["tasks"]
                       if item["id"] == task["id"])
        self.assertEqual(current["version"], task["version"] + 1)
        self.assertIn(current["title"], {"winner-0", "winner-1"})

    def test_revert_preserves_disjoint_edit_and_blocks_later_touch(self) -> None:
        task = self._task(detail="original detail")
        first = self.admin.post(f"/api/task/{task['id']}", json={
            "version": task["version"], "title": "first title",
        }).get_json()
        second = self.admin.post(f"/api/task/{task['id']}", json={
            "version": first["version"], "detail": "later detail",
        }).get_json()
        revisions = self.admin.get(f"/api/task/{task['id']}/revisions").get_json()["revisions"]
        title_revision = next(item for item in revisions if item["after"].get("title") == "first title")
        reverted = self.admin.post(
            f"/api/task/{task['id']}/revisions/{title_revision['id']}/revert",
            json={"version": second["version"]},
        )
        self.assertEqual(reverted.status_code, 200)
        current = next(item for item in self.admin.get("/api/data").get_json()["tasks"]
                       if item["id"] == task["id"])
        self.assertEqual(current["title"], "QA task")
        self.assertEqual(current["detail"], "later detail")

        touched = self.admin.post(f"/api/task/{task['id']}", json={
            "version": reverted.get_json()["version"], "title": "first title",
        })
        self.assertEqual(touched.status_code, 200)
        blocked = self.admin.post(
            f"/api/task/{task['id']}/revisions/{title_revision['id']}/revert",
            json={"version": touched.get_json()["version"]},
        )
        self.assertEqual(blocked.status_code, 409)

    def test_cross_task_revision_and_parent_edit_permission(self) -> None:
        member_id, member = self._member("participant")
        parent = self._task(title="parent")
        child = self._task(title="child", owner_id=member_id, participant_ids=[member_id],
                           parent_id=parent["id"])
        changed = self.admin.post(f"/api/task/{parent['id']}", json={
            "version": parent["version"], "title": "changed parent",
        }).get_json()
        revisions = self.admin.get(f"/api/task/{parent['id']}/revisions").get_json()["revisions"]
        revision_id = next(item["id"] for item in revisions if item["after"].get("title") == "changed parent")
        cross = self.admin.post(
            f"/api/task/{child['id']}/revisions/{revision_id}/revert",
            json={"version": child["version"]},
        )
        self.assertEqual(cross.status_code, 404)
        allowed = member.post(f"/api/task/{child['id']}", json={
            "version": child["version"], "parent_id": parent["id"],
        })
        self.assertEqual(allowed.status_code, 200)
        cycle = self.admin.post(f"/api/task/{parent['id']}", json={
            "version": changed["version"], "parent_id": child["id"],
        })
        self.assertEqual(cycle.status_code, 400)

    def test_inactive_owner_remains_historical_on_unrelated_edit(self) -> None:
        owner_id, _ = self._member("historical-owner")
        task = self._task(owner_id=owner_id)
        disabled = self.admin.post(f"/api/member/{owner_id}/toggle")
        self.assertEqual(disabled.status_code, 200)
        edited = self.admin.post(f"/api/task/{task['id']}", json={
            "version": task["version"], "title": "edited after deactivation",
        })
        self.assertEqual(edited.status_code, 200)
        current = next(item for item in self.admin.get("/api/data").get_json()["tasks"]
                       if item["id"] == task["id"])
        self.assertEqual(current["owner_id"], owner_id)
        member = next(item for item in self.admin.get("/api/data").get_json()["all_members"]
                      if item["id"] == owner_id)
        self.assertEqual(member["is_active"], 0)

    def test_canceled_predecessor_is_warning_only(self) -> None:
        predecessor = self._task(title="canceled predecessor", due_date="2026-11-03",
                                 status="已取消")
        dependent = self._task(title="dependent", predecessor_ids=[predecessor["id"]])
        data = self.admin.get("/api/data").get_json()
        current = next(item for item in data["tasks"] if item["id"] == dependent["id"])
        self.assertTrue(any("已取消" in warning for warning in current["dependency_warnings"]))
        self.assertEqual(current["suggested_start_date"], "2026-11-03")

    def test_delete_is_blocked_by_incoming_dependency(self) -> None:
        predecessor = self._task(title="dependency predecessor")
        self._task(title="dependency consumer", predecessor_ids=[predecessor["id"]])
        blocked = self.admin.post(f"/api/task/{predecessor['id']}/delete")
        self.assertEqual(blocked.status_code, 409)
        visible = next(item for item in self.admin.get("/api/data").get_json()["tasks"]
                       if item["id"] == predecessor["id"])
        self.assertIsNone(visible["deleted_at"])

    def test_three_task_dependency_cycle_is_rejected(self) -> None:
        first = self._task(title="cycle one")
        second = self._task(title="cycle two")
        third = self._task(title="cycle three")
        changed_first = self.admin.post(f"/api/task/{first['id']}", json={
            "version": first["version"], "predecessor_ids": [third["id"]],
        })
        self.assertEqual(changed_first.status_code, 200)
        changed_second = self.admin.post(f"/api/task/{second['id']}", json={
            "version": second["version"], "predecessor_ids": [first["id"]],
        })
        self.assertEqual(changed_second.status_code, 200)
        cycle = self.admin.post(f"/api/task/{third['id']}", json={
            "version": third["version"], "predecessor_ids": [second["id"]],
        })
        self.assertEqual(cycle.status_code, 400)

    def test_member_cannot_mutate_catalogs_or_membership(self) -> None:
        member_id, member = self._member("catalog-outsider")
        group = self.admin.post("/api/groups", json={"name": "QA group"})
        self.assertEqual(group.status_code, 200)
        denied_group = member.post("/api/groups", json={"name": "forbidden group"})
        denied_project = member.post("/api/projects", json={"name": "forbidden project"})
        denied_membership = member.post(f"/api/member/{member_id}/groups", json={
            "group_ids": [group.get_json()["id"]],
        })
        self.assertEqual(denied_group.status_code, 403)
        self.assertEqual(denied_project.status_code, 403)
        self.assertEqual(denied_membership.status_code, 403)

    def test_inactive_member_cannot_be_newly_assigned(self) -> None:
        member_id, _ = self._member("inactive-assignment")
        self.assertEqual(self.admin.post(f"/api/member/{member_id}/toggle").status_code, 200)
        owner = self.admin.post("/api/task", json={"title": "inactive owner", "owner_id": member_id})
        participant = self.admin.post("/api/task", json={
            "title": "inactive participant", "owner_id": 1, "participant_ids": [member_id],
        })
        self.assertEqual(owner.status_code, 400)
        self.assertEqual(participant.status_code, 400)

    def test_participant_requires_owner_and_invalid_catalog_refs_fail(self) -> None:
        member_id, _ = self._member("owner-required")
        no_owner = self.admin.post("/api/task", json={
            "title": "missing owner", "participant_ids": [member_id],
        })
        bad_group = self.admin.post("/api/task", json={"title": "bad group", "group_ids": [999999]})
        bad_project = self.admin.post("/api/task", json={"title": "bad project", "project_id": 999999})
        root = self._task(title="shared root")
        shared = self.admin.post("/api/task", json={"title": "shared parent and predecessor", "parent_id": root["id"], "predecessor_ids": [root["id"]]})
        self.assertEqual(shared.status_code, 200)
        self.assertEqual(no_owner.status_code, 400); self.assertEqual(bad_group.status_code, 400); self.assertEqual(bad_project.status_code, 400)

    def test_legacy_names_conflict_with_new_catalog_ids(self) -> None:
        group_id = self.admin.post("/api/groups", json={"name": "conflict group"}).get_json()["id"]
        project_id = self.admin.post("/api/projects", json={"name": "conflict project"}).get_json()["id"]
        group_conflict = self.admin.post("/api/task", json={
            "title": "group conflict", "group_ids": [group_id], "group_name": "other group",
        })
        project_conflict = self.admin.post("/api/task", json={
            "title": "project conflict", "project_id": project_id, "project": "other project",
        })
        self.assertEqual(group_conflict.status_code, 400)
        self.assertEqual(project_conflict.status_code, 400)

    def test_legacy_update_without_version_uses_current_cas(self) -> None:
        task = self._task(title="legacy update")
        response = self.admin.post(f"/api/task/{task['id']}", json={"title": "legacy updated"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["version"], task["version"] + 1)

    def test_null_float_and_non_integer_versions_are_rejected(self) -> None:
        null_ids = self.admin.post("/api/task", json={"title": "null IDs", "participant_ids": [None]})
        float_ids = self.admin.post("/api/task", json={"title": "float IDs", "group_ids": [1.5]})
        task = self._task(title="version type")
        null_version = self.admin.post(f"/api/task/{task['id']}", json={"version": None, "title": "x"})
        float_version = self.admin.post(f"/api/task/{task['id']}", json={"version": 1.5, "title": "x"})
        bool_version = self.admin.post(f"/api/task/{task['id']}", json={"version": True, "title": "x"})
        self.assertEqual(null_ids.status_code, 400); self.assertEqual(float_ids.status_code, 400)
        self.assertEqual(null_version.status_code, 400); self.assertEqual(float_version.status_code, 400); self.assertEqual(bool_version.status_code, 400)

    def test_malformed_ids_and_json_are_client_errors(self) -> None:
        cases = [
            {"title": "bool owner", "owner_id": True},
            {"title": "float owner", "owner_id": 1.5},
            {"title": "bool participant", "participant_ids": [True]},
            {"title": "duplicate groups", "group_ids": [1, 1]},
        ]
        for payload in cases:
            response = self.admin.post("/api/task", json=payload)
            self.assertIn(response.status_code, {400, 403})
        list_body = self.admin.post("/api/task", json=[{"title": "list body"}])
        self.assertIn(list_body.status_code, {400, 415})
        null_body = self.admin.post("/api/task", json=None)
        self.assertIn(null_body.status_code, {400, 415})


if __name__ == "__main__":
    unittest.main()
