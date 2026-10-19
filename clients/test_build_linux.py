"""Exercise Linux source staging without invoking npm, Cargo or a package builder."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clients import build_linux


class StopBeforeBuild(Exception):
    pass


class LinuxSourceInputsTests(unittest.TestCase):
    def fixture(self, root):
        clients = root / 'clients'
        desktop = clients / 'desktop'
        frontend = clients / 'frontend/dist'
        (desktop / 'icons').mkdir(parents=True)
        frontend.mkdir(parents=True)
        for path in (clients / 'config.json', desktop / 'tauri.conf.json', frontend / 'asset-manifest.json'):
            path.write_text(json.dumps({'version': '1.0.10'}), encoding='utf-8')
        (desktop / 'Cargo.toml').write_text('[package]\nversion = "1.0.10"\n', encoding='utf-8')
        (desktop / 'Cargo.lock').write_text('# synthetic lock\n', encoding='utf-8')
        (desktop / 'package-lock.json').write_text('{}', encoding='utf-8')
        (clients / 'update-public-key.pem').write_bytes(b'TEST ONLY PUBLIC KEY')
        (clients / 'signing').mkdir()
        (clients / 'signing/ui-update-private.pem').write_bytes(b'TEST ONLY PRIVATE KEY')

    def test_source_staging_contains_pinned_key_without_signing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)

            def inspect(*args, **kwargs):
                self.assertEqual(args[0][0], 'npm')
                staging = kwargs['cwd'].parent
                self.assertEqual((staging / 'update-public-key.pem').read_bytes(), b'TEST ONLY PUBLIC KEY')
                self.assertFalse((staging / 'signing').exists())
                self.assertTrue((staging / 'frontend/dist/asset-manifest.json').is_file())
                raise StopBeforeBuild

            with patch.object(build_linux, 'run', side_effect=inspect), self.assertRaises(StopBeforeBuild):
                build_linux.build(root, root / 'output', root / 'cache', {})

    def test_missing_key_stops_before_native_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            (root / 'clients/update-public-key.pem').unlink()
            with patch.object(build_linux, 'run') as runner:
                with self.assertRaisesRegex(RuntimeError, 'update-public-key.pem'):
                    build_linux.build(root, root / 'output', root / 'cache', {})
                runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
