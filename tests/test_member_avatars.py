"""Self-service avatars use isolated databases and synthetic image content only."""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from PIL import Image
from werkzeug.datastructures import MultiDict

from test_tasks import app_module
from modules import member_avatars


def image_bytes(fmt="PNG", size=(96, 64), color="red", **options):
    with Image.new("RGB", size, color) as image, io.BytesIO() as output:
        image.save(output, format=fmt, **options)
        return output.getvalue()


class TestMemberAvatars(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xtyf-avatar-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = patch.dict(app_module.app.config, {"TASK_ATTACHMENTS_DIR": str(self.root)})
        config.start()
        self.addCleanup(config.stop)
        with app_module.app.app_context():
            db = app_module.get_db()
            self.member_id = db.execute(
                "INSERT INTO members(name,username,password_hash,created_at,must_change_password) "
                "VALUES(?,?,?,? ,0)",
                ("合成头像成员", "avatar_" + uuid4().hex, "not-a-login-password", "2026-10-18"),
            ).lastrowid
            db.commit()
        self.client = self.client_for(self.member_id)

    def client_for(self, member_id):
        client = app_module.app.test_client()
        with client.session_transaction() as session:
            session["uid"] = member_id
            session["session_version"] = 0
        return client

    def upload(self, client=None, *, content=None, crop=None, name="avatar.png", extra=None):
        data = {
            "avatar": (io.BytesIO(content if content is not None else image_bytes()), name),
            "crop": json.dumps(crop if crop is not None else {"x": 16, "y": 0, "size": 64}),
        }
        data.update(extra or {})
        return (client or self.client).post(
            "/api/account/avatar", data=data, content_type="multipart/form-data",
        )

    def key(self):
        with app_module.app.app_context():
            return app_module.get_db().execute(
                "SELECT avatar_key FROM members WHERE id=?", (self.member_id,),
            ).fetchone()[0]

    def stored_files(self):
        directory = self.root / "avatars"
        return sorted(path.name for path in directory.iterdir()) if directory.exists() else []

    def test_default_upload_persists_private_webp_and_delete_restores_default(self):
        profile = self.client.get("/api/account/profile").get_json()["profile"]
        self.assertIsNone(profile["avatar_url"])
        self.assertEqual(self.client.get("/api/account/avatar").status_code, 404)
        saved = self.upload()
        self.assertEqual(saved.status_code, 200, saved.get_json())
        url = saved.get_json()["avatar_url"]
        self.assertTrue(url.startswith("/api/account/avatar?v="))
        self.assertEqual(self.stored_files(), [self.key()])
        fresh_client = self.client_for(self.member_id)
        self.assertEqual(fresh_client.get("/api/account/profile").get_json()["profile"]["avatar_url"], url)
        fetched = fresh_client.get(url)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.mimetype, "image/webp")
        self.assertEqual(fetched.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", fetched.headers["Cache-Control"])
        with Image.open(io.BytesIO(fetched.data)) as output:
            self.assertEqual(output.format, "WEBP")
            self.assertEqual(output.size, (512, 512))
            self.assertFalse(output.getexif())
        deleted = self.client.delete("/api/account/avatar")
        self.assertEqual(deleted.get_json(), {"ok": True, "avatar_url": None})
        self.assertIsNone(self.key())
        self.assertEqual(self.stored_files(), [])
        self.assertEqual(fresh_client.get(url).status_code, 404)
        self.assertEqual(self.client.delete("/api/account/avatar").status_code, 200)

    def test_replace_removes_only_previous_avatar_and_does_not_change_profile(self):
        self.upload()
        old = self.key()
        before = self.client.get("/api/account/profile").get_json()["profile"]
        updated = self.upload(content=image_bytes(color="blue"))
        self.assertEqual(updated.status_code, 200)
        self.assertNotEqual(self.key(), old)
        self.assertFalse((self.root / "avatars" / old).exists())
        self.assertEqual(self.stored_files(), [self.key()])
        after = self.client.get("/api/account/profile").get_json()["profile"]
        before.pop("avatar_url")
        after.pop("avatar_url")
        self.assertEqual(before, after)

    def test_three_supported_formats_are_decoded_by_content_and_metadata_removed(self):
        for fmt in ("PNG", "JPEG", "WEBP"):
            with self.subTest(fmt=fmt):
                exif = Image.Exif()
                exif[270] = "synthetic private metadata"
                saved = self.upload(content=image_bytes(fmt, exif=exif), name="original.any")
                self.assertEqual(saved.status_code, 200, saved.get_json())
                fetched = self.client.get(saved.get_json()["avatar_url"])
                with Image.open(io.BytesIO(fetched.data)) as output:
                    self.assertFalse(output.getexif())
                    self.assertNotIn("icc_profile", output.info)
                    self.assertNotIn("xmp", output.info)

    def test_exif_rotation_precedes_pixel_coordinate_crop(self):
        with Image.new("RGB", (96, 64), "red") as source, io.BytesIO() as output:
            source.paste("blue", (48, 0, 96, 64))
            exif = Image.Exif()
            exif[274] = 6
            source.save(output, "JPEG", quality=100, exif=exif)
            content = output.getvalue()
        # Orientation 6 rotates clockwise: the right blue half becomes the bottom.
        saved = self.upload(content=content, crop={"x": 8, "y": 48, "size": 48})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        with Image.open(io.BytesIO(self.client.get(saved.get_json()["avatar_url"]).data)) as output:
            red, _, blue = output.convert("RGB").getpixel((256, 256))
            self.assertLess(red, 30)
            self.assertGreater(blue, 200)
            self.assertNotIn(274, output.getexif())
        rejected = self.upload(content=content, crop={"x": 33, "y": 0, "size": 32})
        self.assertEqual(rejected.status_code, 400)

    def test_invalid_crop_does_not_replace_existing_avatar(self):
        self.upload()
        old = self.key()
        invalid = [None, [], {}, {"x": 0, "y": 0}, {"x": 0, "y": 0, "size": 64, "other": 1}]
        invalid += [{"x": x, "y": y, "size": size} for x, y, size in (
            (-1, 0, 64), (0, -1, 64), (0, 0, 0), (0, 0, -1), (33, 0, 64),
            (0, 1, 64), (0, 0, 65), (False, 0, 32), (0, 0.5, 32), ("0", 0, 32),
        )]
        for crop in invalid:
            with self.subTest(crop=crop):
                saved = self.upload(extra={"crop": json.dumps(crop)})
                self.assertEqual(saved.status_code, 400, saved.get_json())
                self.assertEqual(self.key(), old)
                self.assertEqual(self.stored_files(), [old])
        self.assertEqual(self.upload(crop={"x": 95, "y": 63, "size": 1}).status_code, 200)

    def test_malformed_duplicate_and_cross_user_fields_rejected(self):
        for extra in ({"crop": "{"}, {"member_id": "1"}, {"user_id": "1"}, {"id": "1"}):
            with self.subTest(extra=extra):
                self.assertEqual(self.upload(extra=extra).status_code, 400)
        for data in (
            {}, {"crop": "{}"}, {"avatar": (io.BytesIO(image_bytes()), "one.png")},
            MultiDict([("crop", "{}"), ("crop", "{}"),
                       ("avatar", (io.BytesIO(image_bytes()), "one.png"))]),
            MultiDict([("crop", "{}"), ("avatar", (io.BytesIO(image_bytes()), "one.png")),
                       ("avatar", (io.BytesIO(image_bytes()), "two.png"))]),
        ):
            response = self.client.post("/api/account/avatar", data=data, content_type="multipart/form-data")
            self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.client.delete("/api/account/avatar?member_id=1").status_code, 400)
        self.assertEqual(self.client.delete("/api/account/avatar", json={"member_id": 1}).status_code, 400)
        self.assertEqual(self.stored_files(), [])
        self.assertIsNone(self.key())

    def test_authentication_csrf_first_login_and_deactivation_are_enforced(self):
        anonymous = app_module.app.test_client()
        self.assertEqual(anonymous.get("/api/account/avatar").status_code, 401)
        self.assertEqual(self.upload(anonymous).status_code, 401)
        self.assertEqual(anonymous.delete("/api/account/avatar").status_code, 401)
        self.assertEqual(self.upload(extra={}).status_code, 200)
        old = self.key()
        bad_csrf = self.client.post("/api/account/avatar", headers={"X-CSRF-Token": "invalid"})
        self.assertEqual(bad_csrf.status_code, 403)
        self.assertEqual(self.client.delete("/api/account/avatar", headers={"X-CSRF-Token": "invalid"}).status_code, 403)
        for field, value in (("must_change_password", 1), ("is_active", 0)):
            with app_module.app.app_context():
                db = app_module.get_db()
                db.execute(f"UPDATE members SET {field}=? WHERE id=?", (value, self.member_id))
                db.commit()
            expected = 403 if field == "must_change_password" else 401
            self.assertEqual(self.upload().status_code, expected)
            self.assertEqual(self.client.delete("/api/account/avatar").status_code, expected)
            self.assertEqual(self.key(), old)

    def test_download_and_modifications_stay_with_logged_in_member(self):
        uploaded = self.upload()
        key = self.key()
        with app_module.app.app_context():
            db = app_module.get_db()
            other_id = db.execute(
                "INSERT INTO members(name,username,password_hash,created_at) VALUES(?,?,?,?)",
                ("另一合成成员", "avatar_other_" + uuid4().hex, "unused", "2026-10-18"),
            ).lastrowid
            db.commit()
        other = self.client_for(other_id)
        self.assertEqual(other.get(uploaded.get_json()["avatar_url"]).status_code, 404)
        self.assertEqual(other.delete("/api/account/avatar").status_code, 200)
        self.assertEqual(self.key(), key)
        self.assertEqual(self.upload(other, content=image_bytes(color="blue")).status_code, 200)
        self.assertEqual(self.key(), key)
        self.assertEqual(len(self.stored_files()), 2)

    def test_invalid_content_formats_animation_and_size_limits_rejected(self):
        with Image.new("RGB", (16, 16), "red") as first, Image.new("RGB", (16, 16), "blue") as second:
            animated = io.BytesIO()
            first.save(animated, "PNG", save_all=True, append_images=[second], duration=100, loop=0)
        bad_files = [
            (b"", 400), (b"<svg xmlns='http://www.w3.org/2000/svg'/>e", 400),
            (b"not a picture", 400), (image_bytes()[:30], 400),
            (image_bytes("BMP"), 400), (image_bytes("GIF"), 400), (animated.getvalue(), 400),
            (b"x" * (10 * 1024 * 1024 + 1), 413),
            (image_bytes(size=(10001, 1)), 400), (image_bytes(size=(5000, 4001)), 400),
        ]
        for index, (content, status) in enumerate(bad_files):
            with self.subTest(index=index):
                self.assertEqual(self.upload(content=content).status_code, status)
                self.assertIsNone(self.key())
                self.assertEqual(self.stored_files(), [])

    def test_storage_failures_preserve_previous_reference_and_picture(self):
        self.upload()
        old = self.key()
        original = (self.root / "avatars" / old).read_bytes()
        for target in ("modules.member_avatars.stage_images", "modules.image_storage.os.replace"):
            with self.subTest(target=target), patch(target, side_effect=OSError("synthetic disk failure")):
                response = self.upload(content=image_bytes(color="blue"))
            self.assertEqual(response.status_code, 500)
            self.assertEqual(self.key(), old)
            self.assertEqual((self.root / "avatars" / old).read_bytes(), original)
            self.assertEqual(self.stored_files(), [old])

    def test_database_commit_failure_rolls_back_reference_and_new_file(self):
        self.upload()
        old = self.key()
        real_get_db = member_avatars.get_db

        class CommitFailure:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                raise sqlite3.OperationalError("synthetic commit failure")

        with patch("modules.member_avatars.get_db", side_effect=lambda: CommitFailure(real_get_db())):
            response = self.upload(content=image_bytes(color="blue"))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.key(), old)
        self.assertEqual(self.stored_files(), [old])
        with patch("modules.member_avatars.get_db", side_effect=lambda: CommitFailure(real_get_db())):
            response = self.client.delete("/api/account/avatar")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.key(), old)
        self.assertEqual(self.stored_files(), [old])

    def test_missing_file_returns_default_compatible_not_found(self):
        self.upload()
        (self.root / "avatars" / self.key()).unlink()
        self.assertEqual(self.client.get("/api/account/avatar").status_code, 404)
        self.assertEqual(self.client.delete("/api/account/avatar").status_code, 200)

    def test_overlapping_replacements_leave_last_committed_picture_readable(self):
        self.upload()
        original_key = self.key()
        first_committed = threading.Event()
        finish_first_cleanup = threading.Event()
        real_remove = member_avatars._remove_old

        def pause_first_cleanup(key):
            if key == original_key:
                first_committed.set()
                if not finish_first_cleanup.wait(15):
                    raise AssertionError("second upload did not finish")
            real_remove(key)

        first_client = self.client_for(self.member_id)
        second_client = self.client_for(self.member_id)
        with patch("modules.member_avatars._remove_old", side_effect=pause_first_cleanup):
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(self.upload, first_client, content=image_bytes(color="blue"))
                try:
                    self.assertTrue(first_committed.wait(15), "first upload did not commit")
                    second = self.upload(second_client, content=image_bytes(color="green"))
                    self.assertEqual(second.status_code, 200, second.get_json())
                    final_key = self.key()
                finally:
                    finish_first_cleanup.set()
                self.assertEqual(first.result(timeout=15).status_code, 200)
        self.assertEqual(self.key(), final_key)
        self.assertEqual(self.stored_files(), [final_key])
        self.assertEqual(self.client.get(second.get_json()["avatar_url"]).status_code, 200)

    def test_migration_is_repeatable_and_preserves_existing_member_data(self):
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE members(id INTEGER PRIMARY KEY,name TEXT)")
            db.execute("INSERT INTO members VALUES(17,'合成旧成员')")
            member_avatars.migrate(db)
            self.assertEqual(db.execute("SELECT id,name,avatar_key FROM members").fetchone(),
                             (17, "合成旧成员", None))
            db.execute("UPDATE members SET avatar_key='synthetic.webp' WHERE id=17")
            db.commit()
            member_avatars.migrate(db)
            self.assertEqual(db.execute("SELECT id,name,avatar_key FROM members").fetchone(),
                             (17, "合成旧成员", "synthetic.webp"))
            self.assertEqual(sum(row[1] == "avatar_key" for row in db.execute("PRAGMA table_info(members)")), 1)


if __name__ == "__main__":
    unittest.main()
