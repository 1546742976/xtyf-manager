"""Collaboration task API behavior through the real Flask surface."""
import csv
import io
import unittest

from test_tasks import app_module


class TestCollaborationBackend(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        login = self.admin.post("/login", json={
            "username": "admin", "password": "ci-test-password-only",
        })
        self.assertTrue(login.get_json()["ok"])

    def _member(self, suffix: str) -> tuple[int, object]:
        response = self.admin.post("/api/member", json={
            "name": "协作成员" + suffix, "username": "collab_" + suffix,
            "password": "collab-password", "group_ids": [],
        })
        member_id = response.get_json()["id"]
        client = app_module.app.test_client()
        logged_in = client.post("/login", json={
            "username": "collab_" + suffix, "password": "collab-password",
        })
        self.assertTrue(logged_in.get_json()["ok"])
        return member_id, client

    def test_catalogs_relations_and_participant_edit(self):
        # Given: two managed catalogs and an active participant.
        group_id = self.admin.post("/api/groups", json={"name": "协作组A"}).get_json()["id"]
        project_id = self.admin.post("/api/projects", json={"name": "协作项目A"}).get_json()["id"]
        member_id, member_client = self._member("catalog")

        # When: an admin creates a task and its participant changes all non-parent settings.
        created = self.admin.post("/api/task", json={
            "title": "协作任务A", "owner_id": member_id,
            "participant_ids": [member_id], "group_ids": [group_id],
            "project_id": project_id,
        })
        self.assertEqual(created.status_code, 200)
        payload = created.get_json()
        denied = member_client.post(f"/api/task/{payload['id']}", json={
            "version": payload["version"], "title": "参与者不能移除自己的归属",
            "owner_id": member_id, "participant_ids": [],
            "group_ids": [group_id], "project_id": project_id,
        })
        self.assertEqual(denied.status_code, 403)
        updated = member_client.post(f"/api/task/{payload['id']}", json={
            "version": payload["version"], "title": "参与者已修改",
        })

        # Then: the version advances and the expanded task is returned by the aggregate API.
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["version"], 2)
        task = next(item for item in self.admin.get("/api/data").get_json()["tasks"]
                    if item["id"] == payload["id"])
        self.assertEqual(task["title"], "参与者已修改")
        self.assertEqual(task["group_ids"], [group_id])
        self.assertEqual(task["project_id"], project_id)

    def test_version_conflict_and_revision_revert_conflict(self):
        # Given: a task changed in two separate structural revisions.
        created = self.admin.post("/api/task", json={"title": "版本任务"}).get_json()
        first = self.admin.post(f"/api/task/{created['id']}", json={
            "version": created["version"], "title": "第一次修改",
        }).get_json()
        stale = self.admin.post(f"/api/task/{created['id']}", json={
            "version": created["version"], "priority": "高",
        })
        second = self.admin.post(f"/api/task/{created['id']}", json={
            "version": first["version"], "title": "第二次修改",
        }).get_json()
        revisions = self.admin.get(
            f"/api/task/{created['id']}/revisions"
        ).get_json()["revisions"]
        first_title_revision = next(
            revision for revision in reversed(revisions)
            if revision["after"].get("title") == "第一次修改"
        )

        # When: stale update and undo of a field touched later are attempted.
        reverted = self.admin.post(
            f"/api/task/{created['id']}/revisions/{first_title_revision['id']}/revert",
            json={"version": second["version"]},
        )

        # Then: both fail as conflicts without overwriting the current task.
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(reverted.status_code, 409)

    def test_graph_validation_logical_delete_and_dependency_warnings(self):
        # Given: a predecessor, its dependent child, and a separate parent.
        predecessor = self.admin.post("/api/task", json={
            "title": "前置任务", "due_date": "2026-11-03", "status": "进行中",
        }).get_json()
        parent = self.admin.post("/api/task", json={"title": "父任务"}).get_json()
        child = self.admin.post("/api/task", json={
            "title": "子任务", "parent_id": parent["id"],
            "predecessor_ids": [predecessor["id"]], "start_date": "2026-11-01",
        }).get_json()

        # When: deletion and a parent cycle are attempted.
        blocked_delete = self.admin.post(f"/api/task/{parent['id']}/delete")
        cycle = self.admin.post(f"/api/task/{parent['id']}", json={
            "version": parent["version"], "parent_id": child["id"],
        })

        # Then: graph integrity is enforced and dependency advice is read-only output.
        self.assertEqual(blocked_delete.status_code, 409)
        self.assertEqual(cycle.status_code, 400)
        task = next(item for item in self.admin.get("/api/data").get_json()["tasks"]
                    if item["id"] == child["id"])
        self.assertIsNone(task["suggested_start_date"], "Only the main predecessor supplies a date suggestion")
        self.assertTrue(task["dependency_warnings"])

    def test_same_task_can_be_parent_and_predecessor(self):
        # Given: one existing task serving two independent graph roles.
        anchor = self.admin.post("/api/task", json={"title": "双重关系锚点"}).get_json()

        # When: a child also declares that parent as a scheduling predecessor.
        response = self.admin.post("/api/task", json={
            "title": "双重关系子任务", "parent_id": anchor["id"],
            "predecessor_ids": [anchor["id"]],
        })

        # Then: separate tree and dependency edges are both accepted.
        self.assertEqual(response.status_code, 200)

    def test_legacy_catalog_projections_follow_create_edit_and_revert(self):
        # Given: a task created through legacy catalog names.
        group_a = self.admin.post("/api/groups", json={"name": "投影组A"}).get_json()["id"]
        group_b = self.admin.post("/api/groups", json={"name": "投影组B"}).get_json()["id"]
        project_a = self.admin.post("/api/projects", json={"name": "投影项目A"}).get_json()["id"]
        project_b = self.admin.post("/api/projects", json={"name": "投影项目B"}).get_json()["id"]
        created = self.admin.post("/api/task", json={
            "title": "兼容投影任务", "group_name": "投影组A", "project": "投影项目A",
        }).get_json()

        # When: normalized IDs replace both catalogs and that revision is reverted.
        updated = self.admin.post(f"/api/task/{created['id']}", json={
            "version": created["version"], "group_ids": [group_b], "project_id": project_b,
        }).get_json()
        rows_after_edit = list(csv.reader(io.StringIO(
            self.admin.get("/export.csv").get_data(as_text=True).lstrip("\ufeff")
        )))
        revision = next(item for item in self.admin.get(
            f"/api/task/{created['id']}/revisions"
        ).get_json()["revisions"] if set(item["after"]) == {"group_ids", "project_id"})
        reverted = self.admin.post(
            f"/api/task/{created['id']}/revisions/{revision['id']}/revert",
            json={"version": updated["version"]},
        )
        rows_after_revert = list(csv.reader(io.StringIO(
            self.admin.get("/export.csv").get_data(as_text=True).lstrip("\ufeff")
        )))

        # Then: old CSV columns always project the current normalized names.
        header = rows_after_edit[0]
        edit_row = next(row for row in rows_after_edit[1:] if row[1] == "兼容投影任务")
        revert_row = next(row for row in rows_after_revert[1:] if row[1] == "兼容投影任务")
        self.assertEqual(reverted.status_code, 200)
        self.assertEqual((edit_row[header.index("组别")], edit_row[header.index("项目")]),
                         ("投影项目B", "投影项目B"))
        self.assertEqual((revert_row[header.index("组别")], revert_row[header.index("项目")]),
                         ("投影项目A", "投影项目A"))
        task = next(item for item in self.admin.get("/api/data").get_json()["tasks"]
                    if item["id"] == created["id"])
        self.assertEqual((task["group_name"], task["project"], task["group_ids"], task["project_id"]),
                         ("投影组A", "投影项目A", [group_a], project_a))

    def test_malformed_relationship_updates_are_atomic_client_errors(self):
        # Given: an authenticated owner and a versioned task.
        owner_id, owner = self._member("malformed")
        created = self.admin.post("/api/task", json={
            "title": "边界任务", "owner_id": owner_id, "participant_ids": [owner_id],
        }).get_json()

        # When: malformed arrays, oversized IDs, and invalid versions cross the HTTP boundary.
        cases = (
            {"participant_ids": [False]}, {"group_ids": [1.5]},
            {"predecessor_ids": [None]}, {"participant_ids": "not-an-array"},
            {"owner_id": 18446744073709551616},
            {"predecessor_ids": [18446744073709551616]},
            {"version": 18446744073709551616},
        )
        statuses = []
        for values in cases:
            response = owner.post(f"/api/task/{created['id']}", json={
                "version": created["version"], **values,
            })
            statuses.append(response.status_code)

        # Then: every request is a 400 and no partial relationship/version write survives.
        self.assertEqual(statuses, [400] * len(cases))
        task = next(item for item in self.admin.get("/api/data").get_json()["tasks"]
                    if item["id"] == created["id"])
        self.assertEqual((task["version"], task["owner_id"], task["participant_ids"]),
                         (created["version"], owner_id, [owner_id]))
        self.assertEqual(self.admin.post("/api/task", json={
            "title": "布尔负责人", "owner_id": False,
        }).status_code, 400)
        self.assertEqual(self.admin.post("/api/task", json={
            "title": "超大负责人", "owner_id": 18446744073709551616,
        }).status_code, 400)
        self.assertEqual(owner.post(
            "/api/task/18446744073709551616", json={"version": created["version"]}
        ).status_code, 400)

    def test_public_workspace_members_exclude_usernames(self):
        # Given: an ordinary authenticated member.
        _, member = self._member("public")

        # When: the shared workspace payload is read.
        payload = member.get("/api/data").get_json()

        # Then: neither active nor historical public directory entries expose usernames.
        self.assertTrue(payload["members"])
        self.assertTrue(payload["all_members"])
        self.assertTrue(all("username" not in item for item in payload["members"]))
        self.assertTrue(all("username" not in item for item in payload["all_members"]))

    def test_stale_delete_has_no_side_effects_and_legacy_delete_uses_snapshot(self):
        # Given: a task whose version advanced after the deleting client read it.
        created = self.admin.post("/api/task", json={"title": "删除版本任务"}).get_json()
        updated = self.admin.post(f"/api/task/{created['id']}", json={
            "version": created["version"], "title": "删除版本任务已更新",
        }).get_json()
        logs_before = self.admin.get(f"/api/task/{created['id']}/logs").get_json()["logs"]
        revisions_before = self.admin.get(
            f"/api/task/{created['id']}/revisions"
        ).get_json()["revisions"]

        # When: stale and legacy delete requests are submitted in sequence.
        stale = self.admin.post(f"/api/task/{created['id']}/delete", json={
            "version": created["version"],
        })
        logs_after_stale = self.admin.get(f"/api/task/{created['id']}/logs").get_json()["logs"]
        revisions_after_stale = self.admin.get(
            f"/api/task/{created['id']}/revisions"
        ).get_json()["revisions"]
        present_after_stale = any(item["id"] == created["id"]
                                  for item in self.admin.get("/api/data").get_json()["tasks"])
        legacy = self.admin.post(f"/api/task/{created['id']}/delete", json={})

        # Then: stale CAS is side-effect-free and missing version deletes the locked snapshot.
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(logs_after_stale, logs_before)
        self.assertEqual(revisions_after_stale, revisions_before)
        self.assertTrue(present_after_stale)
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(legacy.get_json()["version"], updated["version"] + 1)
        self.assertEqual(self.admin.get(f"/api/task/{created['id']}/logs").status_code, 404)

    def test_member_group_name_projections_follow_catalog_rename(self):
        # Given: a multi-group member created from normalized IDs.
        group_a = self.admin.post("/api/groups", json={"name": "成员投影A"}).get_json()["id"]
        group_b = self.admin.post("/api/groups", json={"name": "成员投影B"}).get_json()["id"]
        member = self.admin.post("/api/member", json={
            "name": "成员投影用户", "username": "member-projection",
            "password": "member-projection-password", "group_ids": [group_a, group_b],
        }).get_json()

        # When: one normalized catalog name changes.
        renamed = self.admin.post(f"/api/groups/{group_a}", json={"name": "成员投影C"})
        workspace = self.admin.get("/api/data").get_json()
        managed = self.admin.get("/api/members").get_json()["members"]
        csv_rows = list(csv.reader(io.StringIO(
            self.admin.get("/api/members/export.csv").get_data(as_text=True).lstrip("\ufeff")
        )))

        # Then: every legacy display column derives the renamed normalized memberships.
        expected = "成员投影C;成员投影B"
        active = next(item for item in workspace["members"] if item["id"] == member["id"])
        historical = next(item for item in workspace["all_members"] if item["id"] == member["id"])
        managed_member = next(item for item in managed if item["id"] == member["id"])
        header = csv_rows[0]
        csv_member = next(row for row in csv_rows[1:] if row[header.index("ID")] == str(member["id"]))
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(active["group_name"], expected)
        self.assertEqual(historical["group_name"], expected)
        self.assertEqual(managed_member["group_name"], expected)
        self.assertEqual(csv_member[header.index("专业组")], expected)


if __name__ == "__main__":
    unittest.main()
