"""Task screenshot attachment API integration scenarios."""
from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

_ATTACHMENTS_TEMP = tempfile.TemporaryDirectory()
os.environ["TASK_ATTACHMENTS_DIR"] = _ATTACHMENTS_TEMP.name

from attachment_test_support import create_member, image_bytes, upload_payload
from test_tasks import app_module


class TestTaskAttachments(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(_ATTACHMENTS_TEMP.name)
        app_module.app.config["TASK_ATTACHMENTS_DIR"] = str(cls.root)

    def setUp(self) -> None:
        self.root = Path(_ATTACHMENTS_TEMP.name) / uuid4().hex
        self.root.mkdir()
        app_module.app.config["TASK_ATTACHMENTS_DIR"] = str(self.root)
        self.admin = app_module.app.test_client()
        login = self.admin.post("/login", json={
            "username": "admin", "password": "ci-test-password-only",
        })
        self.assertTrue(login.get_json()["ok"])
        created = self.admin.post("/api/task", json={"title": "截图测试任务"})
        self.assertEqual(created.status_code, 200)
        self.task_id = created.get_json()["id"]

    def _upload(self, client=None, *, content: bytes | None = None, name: str = "screen.png"):
        actor = client or self.admin
        return actor.post(
            f"/api/task/{self.task_id}/attachments",
            data={
                "description": "进度截图",
                "files": upload_payload(content or image_bytes(), name),
            },
            content_type="multipart/form-data",
        )

    def _active_rows(self) -> list[sqlite3.Row]:
        with app_module.app.app_context():
            return app_module.get_db().execute(
                "SELECT * FROM task_attachments WHERE task_id=? AND deleted_at IS NULL",
                (self.task_id,),
            ).fetchall()

    def test_upload_list_and_protected_download(self) -> None:
        # Given: a JPEG whose EXIF rotates its dimensions.
        jpeg = image_bytes("JPEG", (8, 16), exif_orientation=6)

        # When: an administrator uploads and another logged-in member downloads it.
        uploaded = self._upload(content=jpeg, name="phone.jpg")
        unrelated = create_member(app_module, self.admin)

        # Then: metadata is normalized, storage is opaque, and reads remain authenticated.
        self.assertEqual(uploaded.status_code, 200)
        attachment = uploaded.get_json()["attachments"][0]
        self.assertEqual((attachment["width"], attachment["height"]), (16, 8))
        self.assertNotIn(str(self.root), str(attachment))
        listed = unrelated.client.get(f"/api/task/{self.task_id}/attachments")
        self.assertEqual(listed.status_code, 200)
        downloaded = unrelated.client.get(attachment["url"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.mimetype, "image/jpeg")
        self.assertEqual(downloaded.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", downloaded.headers["Content-Disposition"])
        with Image.open(io.BytesIO(downloaded.data)) as normalized:
            self.assertNotIn(274, normalized.getexif())

    def test_permissions_and_uploader_delete_after_leaving_task(self) -> None:
        # Given: a participant uploaded one screenshot and then left the task.
        member = create_member(app_module, self.admin, participant_task=self.task_id)
        uploaded = self._upload(member.client).get_json()["attachments"][0]
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute(
                "DELETE FROM task_participants WHERE task_id=? AND member_id=?",
                (self.task_id, member.member_id),
            )
            db.commit()

        # When: the former participant uploads again, then deletes their prior upload.
        denied = self._upload(member.client)
        deleted = member.client.post(
            f"/api/task/{self.task_id}/attachments/{uploaded['id']}/delete"
        )

        # Then: upload membership is current, while delete permission follows ownership.
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self._active_rows(), [])
        self.assertEqual(member.client.get(uploaded["url"]).status_code, 404)
        with app_module.app.app_context():
            logs = app_module.get_db().execute(
                "SELECT note FROM logs WHERE task_id=? AND note IN ('上传截图','删除截图')",
                (self.task_id,),
            ).fetchall()
        self.assertEqual([row["note"] for row in logs], ["上传截图", "删除截图"])

    def test_unrelated_member_cannot_upload_or_delete(self) -> None:
        # Given: an unrelated member and an administrator-owned attachment.
        unrelated = create_member(app_module, self.admin)
        attachment = self._upload().get_json()["attachments"][0]

        # When: the unrelated member attempts both mutations.
        upload = self._upload(unrelated.client)
        delete = unrelated.client.post(
            f"/api/task/{self.task_id}/attachments/{attachment['id']}/delete"
        )

        # Then: neither mutation is authorized.
        self.assertEqual(upload.status_code, 403)
        self.assertEqual(delete.status_code, 403)
        self.assertEqual(len(self._active_rows()), 1)

    def test_rejects_spoofed_oversized_and_too_many_files(self) -> None:
        # Given: three independently invalid multipart requests.
        url = f"/api/task/{self.task_id}/attachments"
        spoof = {"files": upload_payload(b"not an image")}
        oversized = {"files": upload_payload(b"x" * (10 * 1024 * 1024 + 1))}
        too_many = {
            "files": [upload_payload(image_bytes(), f"{index}.png") for index in range(6)]
        }

        # When: each request reaches the real upload boundary.
        responses = [
            self.admin.post(url, data=data, content_type="multipart/form-data")
            for data in (spoof, oversized, too_many)
        ]

        # Then: every request is rejected without metadata or files.
        self.assertEqual([response.status_code for response in responses], [400, 413, 400])
        self.assertEqual(self._active_rows(), [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_partial_stage_write_is_cleaned(self) -> None:
        # Given: the filesystem writes a partial staging file and then raises.
        real_open = Path.open

        class PartialWrite:
            def __init__(self, path: Path):
                self.stream = real_open(path, "xb")

            def __enter__(self):
                return self

            def __exit__(self, error_type, error, traceback):
                self.stream.close()

            def write(self, content: bytes) -> int:
                self.stream.write(content[:8])
                raise OSError("injected partial write")

        def partial_open(path: Path, mode: str):
            return PartialWrite(path) if mode == "xb" else real_open(path, mode)

        # When: a valid upload reaches staging.
        with patch("modules.image_storage.Path.open", autospec=True, side_effect=partial_open):
            response = self._upload()

        # Then: the response fails and the partial file is removed.
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._active_rows(), [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_mixed_batch_and_write_failure_are_atomic(self) -> None:
        # Given: one mixed valid/invalid batch and one two-file valid batch.
        url = f"/api/task/{self.task_id}/attachments"
        mixed = {"files": [
            upload_payload(image_bytes(), "valid.png"),
            upload_payload(b"broken", "broken.png"),
        ]}
        valid = {"files": [
            upload_payload(image_bytes(), "one.png"),
            upload_payload(image_bytes(), "two.png"),
        ]}

        # When: validation fails, then the second final file move fails.
        mixed_response = self.admin.post(url, data=mixed, content_type="multipart/form-data")
        from modules import image_storage
        real_replace = image_storage.os.replace
        calls = 0

        def fail_second_move(source: str | Path, destination: str | Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
            real_replace(source, destination)

        with patch("modules.image_storage.os.replace", side_effect=fail_second_move):
            write_response = self.admin.post(url, data=valid, content_type="multipart/form-data")

        # Then: neither failure leaves rows, final files, or staging files.
        self.assertEqual(mixed_response.status_code, 400)
        self.assertEqual(write_response.status_code, 500)
        self.assertEqual(self._active_rows(), [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_delete_move_failure_preserves_active_attachment(self) -> None:
        # Given: one active attachment whose delete rename will fail.
        attachment = self._upload().get_json()["attachments"][0]

        # When: the filesystem refuses the staged delete.
        with patch("modules.task_attachments.os.replace", side_effect=OSError("injected delete failure")):
            response = self.admin.post(
                f"/api/task/{self.task_id}/attachments/{attachment['id']}/delete"
            )

        # Then: active metadata and downloadable content remain intact.
        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(self._active_rows()), 1)
        self.assertEqual(self.admin.get(attachment["url"]).status_code, 200)

    def test_missing_backing_file_keeps_active_metadata(self) -> None:
        # Given: an attachment whose private backing file was externally removed.
        attachment = self._upload().get_json()["attachments"][0]
        with app_module.app.app_context():
            row = app_module.get_db().execute(
                "SELECT stored_name FROM task_attachments WHERE id=?", (attachment["id"],)
            ).fetchone()
        (self.root / row["stored_name"]).unlink()

        # When: the file is downloaded and deleted through the API.
        downloaded = self.admin.get(attachment["url"])
        deleted = self.admin.post(
            f"/api/task/{self.task_id}/attachments/{attachment['id']}/delete"
        )

        # Then: both responses are contextual 404s and metadata remains repairable.
        self.assertEqual(downloaded.status_code, 404)
        self.assertIn("缺失", downloaded.get_json()["error"])
        self.assertEqual(deleted.status_code, 404)
        self.assertEqual(len(self._active_rows()), 1)

    def test_database_failure_cleans_files_and_rolls_back_logs(self) -> None:
        # Given: a database trigger rejects attachment metadata inserts.
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute(
                "CREATE TEMP TRIGGER reject_attachment BEFORE INSERT ON task_attachments "
                "BEGIN SELECT RAISE(FAIL, 'injected database failure'); END"
            )

            # When: a valid upload reaches persistence on that same request connection.
            with patch("modules.task_attachments.get_db", return_value=db):
                response = self._upload()

            # Then: metadata, task logs, and disk files are all absent.
            logs = db.execute(
                "SELECT * FROM logs WHERE task_id=? AND changes LIKE '%截图%'", (self.task_id,)
            ).fetchall()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._active_rows(), [])
        self.assertEqual(logs, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_auth_missing_resources_and_deleted_task(self) -> None:
        # Given: an anonymous client, a missing attachment, and a deleted task.
        anonymous = app_module.app.test_client()
        missing_url = f"/api/task/{self.task_id}/attachments/999999"

        # When: each unavailable resource is requested.
        unauthenticated = anonymous.get(f"/api/task/{self.task_id}/attachments")
        missing = self.admin.get(missing_url)
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute("UPDATE tasks SET deleted_at=? WHERE id=?", ("2026-10-12 12:00:00", self.task_id))
            db.commit()
        deleted_task = self.admin.get(f"/api/task/{self.task_id}/attachments")

        # Then: authentication and resource boundaries use their distinct statuses.
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(deleted_task.status_code, 404)

    def test_path_ids_reject_sqlite_overflow_without_side_effects(self) -> None:
        # Given: authenticated routes with path IDs outside SQLite's signed range.
        overflow_ids = (2 ** 63, 2 ** 64)
        routes = lambda identifier: (
            ("list", self.admin.get, f"/api/task/{identifier}/attachments", None),
            ("upload", self.admin.post, f"/api/task/{identifier}/attachments", {
                "files": upload_payload(image_bytes()),
            }),
            ("download", self.admin.get, f"/api/task/{self.task_id}/attachments/{identifier}", None),
            ("delete", self.admin.post, f"/api/task/{self.task_id}/attachments/{identifier}/delete", None),
        )

        # When: each attachment endpoint receives an out-of-range task or attachment ID.
        for identifier in overflow_ids:
            for name, method, url, data in routes(identifier):
                with self.subTest(identifier=identifier, endpoint=name):
                    response = method(url, data=data, content_type="multipart/form-data") if data else method(url)

                    # Then: validation returns 400 before database, log, or file changes.
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.get_json()["ok"], False)
        with app_module.app.app_context():
            logs = app_module.get_db().execute(
                "SELECT * FROM logs WHERE task_id=? AND changes LIKE '%截图%'", (self.task_id,)
            ).fetchall()
        self.assertEqual(self._active_rows(), [])
        self.assertEqual(logs, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_path_ids_allow_sqlite_maximum_to_return_missing_resource(self) -> None:
        # Given: SQLite's largest signed ID, which is valid but absent.
        maximum = 2 ** 63 - 1

        # When: every attachment route receives the missing valid ID.
        responses = (
            self.admin.get(f"/api/task/{maximum}/attachments"),
            self.admin.post(
                f"/api/task/{maximum}/attachments",
                data={"files": upload_payload(image_bytes())},
                content_type="multipart/form-data",
            ),
            self.admin.get(f"/api/task/{self.task_id}/attachments/{maximum}"),
            self.admin.post(f"/api/task/{self.task_id}/attachments/{maximum}/delete"),
        )

        # Then: the IDs reach normal missing-resource handling without server errors.
        self.assertEqual([response.status_code for response in responses], [404, 404, 404, 404])
        self.assertEqual(self._active_rows(), [])
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
