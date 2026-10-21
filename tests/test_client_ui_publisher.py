"""Server package composition is tested without building or publishing installers."""
from pathlib import Path
from contextlib import nullcontext
import io
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from deploy.package_release import build_ui_from_head
from deploy import package_release


class TestClientUIPublisher(unittest.TestCase):
    @unittest.skipUnless(shutil.which('rsync'), 'requires rsync on the deployment platform')
    def test_deploy_sync_excludes_client_signing_material_and_preserves_runtime(self):
        script = (package_release.ROOT / 'deploy/deploy.sh').read_text(encoding='utf-8')
        arguments = shlex.split(script.split('sudo rsync', 1)[1].split('\nsudo chmod', 1)[0].replace('\\\n', ''))
        self.assertEqual(arguments[-2:], ['$SRC_DIR/', '$APP_DIR/'])
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / 'source', Path(temporary) / 'target'
            source.mkdir(); target.mkdir()
            excluded = ['clients/signing/ui-update-private.pem', 'clients/signing/android-password.txt',
                        'clients/signing/xtyf-manager.jks', '.build-tools/tool', '.git/config',
                        '.venv/tool', 'clients/desktop/node_modules/tool',
                        'clients/desktop/target/tool', 'clients/android/build/tool', 'dist/setup.exe']
            included = ['app.py', 'clients/update-public-key.pem', 'client-ui-release/update.json']
            for name in excluded + included:
                path = source / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('synthetic source', encoding='utf-8')
            (target / 'data.db').write_bytes(b'existing business data')
            (target / 'secret.key').write_bytes(b'existing session key')
            # Relative paths also work with Windows MSYS rsync, where C: is remote syntax.
            subprocess.run(['rsync', *arguments[:-2], 'source/', 'target/'], cwd=temporary, check=True)
            for name in excluded:
                self.assertFalse((target / name).exists(), name)
            for name in included:
                self.assertEqual((target / name).read_text(), 'synthetic source')
            self.assertEqual((target / 'data.db').read_bytes(), b'existing business data')
            self.assertEqual((target / 'secret.key').read_bytes(), b'existing session key')

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

    def test_unnormalized_staging_root_accepts_selected_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary) / 'staging'
            (staging / 'child').mkdir(parents=True)
            # Reproduce the same canonical/noncanonical mismatch as Windows
            # short temporary paths without depending on 8.3 names being enabled.
            alias = staging / 'child' / '..'

            def build(command, cwd, check):
                self.assertEqual(cwd, staging.resolve())
                self.assertEqual((cwd / 'clients/ui_release.py').read_bytes(), b'HEAD builder')
                output = cwd / 'client-ui-release'
                output.mkdir()
                (output / 'update.json').write_bytes(b'public manifest')

            with patch.object(package_release.tempfile, 'TemporaryDirectory', return_value=nullcontext(str(alias))), \
                    patch.object(package_release.subprocess, 'run', side_effect=build):
                generated = build_ui_from_head({'clients/ui_release.py': b'HEAD builder'}, 'd' * 40, Path(temporary) / 'key')
            self.assertEqual(generated, {'client-ui-release/update.json': b'public manifest'})

    def test_unnormalized_staging_root_still_rejects_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary) / 'staging'
            (staging / 'child').mkdir(parents=True)
            for name in ('../unsafe', 'clients/../../unsafe', 'clients\\unsafe'):
                with self.subTest(name=name), \
                        patch.object(package_release.tempfile, 'TemporaryDirectory', return_value=nullcontext(str(staging / 'child' / '..'))), \
                        patch.object(package_release.subprocess, 'run') as run:
                    with self.assertRaisesRegex(RuntimeError, 'Unsafe source archive path'):
                        build_ui_from_head({name: b'not written'}, 'e' * 40, Path(temporary) / 'key')
                    run.assert_not_called()
                    self.assertFalse((Path(temporary) / 'unsafe').exists())
