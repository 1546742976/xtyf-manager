"""Synthetic, offline recovery point creation, verification and isolation tests."""
import os
import sqlite3
import stat
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from deploy.consistent_snapshot import (
    SnapshotError, create_snapshot, restore_snapshot, verify_snapshot,
)


class TestConsistentSnapshot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xtyf-snapshot-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "live-fixture"
        self.root.mkdir()
        for directory in ("data/funds", "data/invoices/.uploads", "data/task_attachments/42"):
            (self.root / directory).mkdir(parents=True)
        (self.root / "secret.key").write_text("synthetic-secret-not-real", encoding="utf-8")
        (self.root / "data/invoices/.invoice_meta.json").write_text('{"fixture": true}', encoding="utf-8")
        (self.root / "data/invoices/.uploads/pending.pdf").write_bytes(b"%PDF-synthetic-draft")
        (self.root / "data/task_attachments/42/progress.png").write_bytes(b"synthetic-picture")
        (self.root / "data/funds/资金管理记录.txt").write_text("￥0.30", encoding="utf-8")
        for path in ("data.db", "data/funds/funds.db"):
            db = sqlite3.connect(self.root / path)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE entries (name TEXT, cents INTEGER)")
            db.execute("INSERT INTO entries VALUES ('fixture', 30)")
            db.commit()
            db.close()
        self.archive = Path(self.temp.name) / "private-backup.zip"

    def create(self):
        with patch("deploy.consistent_snapshot.service_is_active", return_value=False):
            return create_snapshot(self.root, self.archive, offline_confirmed=True)

    def test_full_snapshot_roundtrip_preserves_db_hidden_files_and_secret(self):
        manifest = self.create()
        self.assertIn("data.db", manifest["files"])
        self.assertIn("data/funds/funds.db", manifest["files"])
        self.assertIn("secret.key", manifest["files"])
        self.assertIn("data/invoices/.invoice_meta.json", manifest["files"])
        self.assertIn("data/invoices/.uploads/pending.pdf", manifest["files"])
        self.assertIn("data/task_attachments/42/progress.png", manifest["files"])
        self.assertEqual(set(manifest["files"]), set(verify_snapshot(self.archive)["files"]))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o600)
        restored = Path(self.temp.name) / "restored"
        restore_snapshot(self.archive, restored)
        for path in ("data.db", "data/funds/funds.db"):
            with closing(sqlite3.connect(restored / path)) as db, db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("SELECT name,cents FROM entries").fetchone(),
                                 ("fixture", 30))
        for relative in ("secret.key", "data/invoices/.invoice_meta.json",
                         "data/invoices/.uploads/pending.pdf",
                         "data/task_attachments/42/progress.png",
                         "data/funds/资金管理记录.txt"):
            self.assertEqual((restored / relative).read_bytes(),
                             (self.root / relative).read_bytes())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE((restored / "secret.key").stat().st_mode), 0o600)
        with self.assertRaises(SnapshotError):
            restore_snapshot(self.archive, restored)

    def test_copy_of_wal_mode_database_is_transactionally_readable(self):
        # Live WAL-mode databases are normalized into clean SQLite .backup files.
        self.create()
        restored = Path(self.temp.name) / "wal-restored"
        restore_snapshot(self.archive, restored)
        self.assertFalse((restored / "data.db-wal").exists())
        self.assertFalse((restored / "data/funds/funds.db-wal").exists())
        with closing(sqlite3.connect(restored / "data.db")) as db, db:
            self.assertEqual(db.execute("SELECT cents FROM entries").fetchone()[0], 30)

    def test_avatar_reference_and_image_survive_snapshot_restore(self):
        import io

        from PIL import Image
        from modules.member_avatars import migrate

        relative = "data/task_attachments/avatars/synthetic-avatar.webp"
        target = self.root / relative
        target.parent.mkdir(parents=True)
        with Image.new("RGB", (512, 512), "blue") as image, io.BytesIO() as output:
            image.save(output, "WEBP")
            target.write_bytes(output.getvalue())
        with closing(sqlite3.connect(self.root / "data.db")) as db:
            db.execute("CREATE TABLE members(id INTEGER PRIMARY KEY,name TEXT)")
            migrate(db)
            db.execute("INSERT INTO members VALUES(7,'合成头像成员','synthetic-avatar.webp')")
            db.commit()
        manifest = self.create()
        self.assertIn(relative, manifest["files"])
        restored = Path(self.temp.name) / "avatar-restored"
        restore_snapshot(self.archive, restored)
        self.assertEqual((restored / relative).read_bytes(), target.read_bytes())
        with closing(sqlite3.connect(restored / "data.db")) as db:
            self.assertEqual(db.execute("SELECT id,name,avatar_key FROM members").fetchone(),
                             (7, "合成头像成员", "synthetic-avatar.webp"))
        with Image.open(restored / relative) as image:
            self.assertEqual(image.size, (512, 512))
            self.assertEqual(image.format, "WEBP")

    def test_collaboration_snapshot_restores_live_and_withdrawn_records_and_original_files(self):
        from modules.collaboration_schema import migrate

        files = {
            'data/task_attachments/collaboration/first.bin': b'original CAD\x00\xff',
            'data/task_attachments/collaboration/first.thumb.png': b'safe separate thumbnail',
            'data/task_attachments/collaboration/withdrawn.bin': b'withdrawn original retained',
        }
        for relative, data in files.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        tables = ('collaboration_topics', 'collaboration_messages', 'collaboration_attachments',
                  'collaboration_submissions', 'collaboration_reads', 'collaboration_changes')
        with closing(sqlite3.connect(self.root / 'data.db')) as db:
            db.execute('CREATE TABLE members(id INTEGER PRIMARY KEY,name TEXT)')
            db.execute("INSERT INTO members VALUES(1,'合成成员')")
            db.commit()
            migrate(db)
            db.execute("INSERT INTO collaboration_topics VALUES(1,'备份话题',1,'合成成员','date',1,'date',NULL,NULL)")
            db.execute("INSERT INTO collaboration_messages VALUES(1,1,1,'合成成员','原正文','date',1,NULL,NULL)")
            db.execute("INSERT INTO collaboration_messages VALUES(2,1,1,'合成成员','撤回正文','date',2,'withdrawn-date',1)")
            db.execute("INSERT INTO collaboration_attachments VALUES(1,1,'设计图.cad','first.bin',14,'digest1','first.thumb.png')")
            db.execute("INSERT INTO collaboration_attachments VALUES(2,2,'撤回资料.zip','withdrawn.bin',27,'digest2',NULL)")
            db.execute("INSERT INTO collaboration_submissions VALUES(1,'submission-1','fingerprint1',1,1)")
            db.execute("INSERT INTO collaboration_reads VALUES(1,1,2)")
            db.execute("INSERT INTO collaboration_changes VALUES(1,1,'message',1,'date')")
            db.execute("INSERT INTO collaboration_changes VALUES(2,1,'withdraw',2,'date')")
            db.commit()
            expected = {table: db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in tables}
        manifest = self.create()
        for relative in files:
            self.assertIn(relative, manifest['files'])
        restored = Path(self.temp.name) / 'collaboration-restored'
        restore_snapshot(self.archive, restored)
        for relative, data in files.items():
            self.assertEqual((restored / relative).read_bytes(), data)
        with closing(sqlite3.connect(restored / 'data.db')) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            for table in tables:
                self.assertEqual(db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall(), expected[table], table)
            self.assertEqual(db.execute('SELECT withdrawn_at FROM collaboration_messages WHERE id=2').fetchone()[0], 'withdrawn-date')

    def test_snapshot_refuses_active_service_missing_ack_and_existing_output(self):
        with self.assertRaisesRegex(SnapshotError, "offline-confirmed"):
            create_snapshot(self.root, self.archive)
        with patch("deploy.consistent_snapshot.service_is_active", return_value=True):
            with self.assertRaisesRegex(SnapshotError, "仍在运行"):
                create_snapshot(self.root, self.archive, offline_confirmed=True)
        self.assertFalse(self.archive.exists())
        self.create()
        before = self.archive.read_bytes()
        with self.assertRaisesRegex(SnapshotError, "已存在"):
            self.create()
        self.assertEqual(self.archive.read_bytes(), before)

    def test_tampered_pdf_or_manifest_is_rejected_before_restore(self):
        self.create()
        broken = Path(self.temp.name) / "tampered.zip"
        with zipfile.ZipFile(self.archive) as original, zipfile.ZipFile(broken, "w") as dst:
            for info in original.infolist():
                payload = original.read(info.filename)
                if info.filename == "data/invoices/.uploads/pending.pdf":
                    payload = b"mutated-document"
                dst.writestr(info, payload)
        with self.assertRaisesRegex(SnapshotError, "校验失败"):
            verify_snapshot(broken)
        target = Path(self.temp.name) / "must-not-be-created"
        with self.assertRaises(SnapshotError):
            restore_snapshot(broken, target)
        self.assertFalse(target.exists())

    def test_symlinked_data_parent_and_recursive_output_are_rejected(self):
        from shutil import move

        (self.root / "data/funds" / "nested.txt").write_text("synthetic", encoding="utf-8")
        inside = self.root / "data/funds" / "full.zip"
        with patch("deploy.consistent_snapshot.service_is_active", return_value=False):
            with self.assertRaisesRegex(SnapshotError, "数据目录"):
                create_snapshot(self.root, inside, offline_confirmed=True)
        self.assertFalse(inside.exists())
        outside = Path(self.temp.name) / "elsewhere-data"
        move(str(self.root / "data"), str(outside))
        try:
            (self.root / "data").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("filesystem does not support symlinks")
        with self.assertRaisesRegex(SnapshotError, "符号链接"):
            self.create()

    def test_symlinked_output_parent_cannot_bypass_recursive_output_check(self):
        alias = Path(self.temp.name) / "backup-alias"
        try:
            alias.symlink_to(self.root / "data/funds", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("filesystem does not support directory symlinks")
        archive = alias / "redirected.zip"
        with patch("deploy.consistent_snapshot.service_is_active", return_value=False):
            with self.assertRaisesRegex(SnapshotError, "数据目录"):
                create_snapshot(self.root, archive, offline_confirmed=True)
        self.assertFalse(archive.exists())

    def test_symlink_and_missing_secret_are_rejected(self):
        (self.root / "secret.key").unlink()
        with self.assertRaises(SnapshotError):
            self.create()
        (self.root / "secret.key").write_bytes(b"fixture")
        link = self.root / "data/invoices/symlink.pdf"
        try:
            link.symlink_to(self.root / "secret.key")
        except (OSError, NotImplementedError):
            self.skipTest("filesystem does not support symlinks")
        with self.assertRaises(SnapshotError):
            self.create()


if __name__ == "__main__":
    unittest.main()
