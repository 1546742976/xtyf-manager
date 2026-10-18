"""Server package composition is tested without building or publishing installers."""
from pathlib import Path
import io
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from deploy.package_release import build_ui_from_head
from deploy import package_release


class TestClientUIPublisher(unittest.TestCase):
    def test_head_archive_preserves_locked_bytes_with_windows_autocrlf(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=source, stderr=subprocess.DEVNULL)
            git('init')
            git('config', 'core.autocrlf', 'true')
            content = b'Public vendor license\nunchanged bytes\n'
            (source / 'LICENSE').write_bytes(content)
            git('add', 'LICENSE')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'synthetic source')
            with patch.object(package_release, 'ROOT', source):
                raw = package_release.archive_head('HEAD')
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                self.assertEqual(archive.extractfile('LICENSE').read(), content)

    def test_only_selected_head_files_are_inputs_and_key_is_not_copied(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary) / "private.pem"
            private.write_text("test key placeholder")
            files = {"templates/account.html": b"exact selected commit", "clients/ui_release.py": b"builder from HEAD"}
            def build(command, cwd, check):
                source = Path(cwd)
                self.assertTrue(check)
                self.assertEqual((source / "templates/account.html").read_bytes(), b"exact selected commit")
                self.assertEqual((source / "clients/ui_release.py").read_bytes(), b"builder from HEAD")
                self.assertFalse((source / "private.pem").exists())
                self.assertEqual(command[command.index("--private-key") + 1], str(private.resolve()))
                self.assertEqual(command[command.index("--source-version") + 1], "a" * 40)
                output = source / "client-ui-release"
                (output / "bundles").mkdir(parents=True)
                (output / "bundles/test.zip").write_bytes(b"synthetic UI")
                (output / "update.json").write_bytes(b"synthetic signature")
            with patch("deploy.package_release.subprocess.run", side_effect=build):
                generated = build_ui_from_head(files, "a" * 40, private)
            self.assertEqual(set(generated), {"client-ui-release/bundles/test.zip", "client-ui-release/update.json"})
            self.assertTrue(private.exists())

    def test_builder_failure_does_not_return_partial_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("deploy.package_release.subprocess.run", side_effect=RuntimeError("build failed")):
                with self.assertRaisesRegex(RuntimeError, "build failed"):
                    build_ui_from_head({"clients/ui_release.py": b""}, "b" * 40, Path(temporary) / "key")

    def test_source_path_cannot_escape_staging(self):
        with patch("deploy.package_release.subprocess.run") as run:
            with self.assertRaisesRegex(RuntimeError, "Unsafe source archive path"):
                build_ui_from_head({"../unsafe": b""}, "c" * 40, Path("key"))
            run.assert_not_called()
