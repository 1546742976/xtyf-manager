"""Release lifecycle tests use fake builders and isolated files, never production artifacts."""
import importlib.util
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('client_release', Path(__file__).with_name('release.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='xtyf-release-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ('clients/desktop', 'clients/android', 'dist/clients/windows', 'dist/clients/android', 'dist/clients/linux'):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.config = {'name': 'Test', 'serverUrl': 'http://example.test', 'version': '1.0.9', 'versionCode': 11}
        for relative in ('clients/config.json',):
            module.write_json(self.root / relative, self.config)
        module.write_json(self.root / 'clients/desktop/package.json', {'name': 'test', 'version': '1.0.9'})
        module.write_json(self.root / 'clients/desktop/package-lock.json', {'version': '1.0.9', 'packages': {'': {'version': '1.0.9'}}})
        module.write_json(self.root / 'clients/desktop/tauri.conf.json', {'version':'1.0.9'})
        (self.root / 'clients/desktop/Cargo.toml').write_text('[package]\nname = \"xtyf-manager\"\nversion = \"1.0.9\"\n')
        (self.root / 'clients/desktop/Cargo.lock').write_text('[[package]]\nname = \"xtyf-manager\"\nversion = \"1.0.9\"\n')
        (self.root / 'clients/frontend/dist').mkdir(parents=True)
        (self.root / 'clients/frontend/dist/asset-manifest.json').write_text('{}')
        (self.root / 'clients/android/AndroidManifest.xml').write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" android:versionCode="11" android:versionName="1.0.9"/>', encoding='utf-8')
        (self.root / 'clients/README.md').write_text('instructions', encoding='utf-8')
        self.output = self.root / 'dist/clients'
        self.old = [
            self.output / 'windows/XTYF-Manager-Setup-1.0.9-x64.exe',
            self.output / 'android/XTYF-Manager-1.0.9.apk',
            self.output / 'linux/XTYF-Manager-1.0.9-arm64.deb',
            self.output / 'linux/XTYF-Manager-1.0.9-x86_64.rpm',
        ]
        for file in self.old:
            file.write_bytes(b'previous installer')
        module.write_json(self.output / 'build-report.json', {'version': '1.0.9', 'android': {'versionCode': 11}})
        (self.output / 'SHA256SUMS.txt').write_text('previous checksums')
        self.original = {relative: (self.root / relative).read_bytes() for relative in (*module.VERSION_FILES, 'dist/clients/build-report.json', 'dist/clients/SHA256SUMS.txt')}
        self.calls = []

    def builder(self, command, root):
        self.calls.append(command)
        # Old packages must remain available while any platform is still building.
        self.assertTrue(all(file.exists() for file in self.old))
        config = module.read_json(root / 'clients/config.json')
        if '--check' in command or '-Check' in command:
            self.assertEqual(config, self.config)
            self.assertFalse(list(self.output.glob('.release-*')))
            return
        if any(Path(str(arg)).name == 'build_frontend.py' for arg in command):
            module.write_json(root / 'clients/frontend/dist/asset-manifest.json', {'version': config['version'], 'protocolVersion': 1, 'files': {'index.html': hashlib.sha256(b'local page').hexdigest()}})
            return
        if '-OutputDirectory' in command:
            out = Path(command[command.index('-OutputDirectory') + 1])
            names = [f'XTYF-Manager-Setup-{config["version"]}-x64.exe']
        else:
            out = Path(command[command.index('--output-dir') + 1])
            if any(Path(str(arg)).name == 'build_linux.py' for arg in command):
                names = [f'XTYF-Manager-{config["version"]}-arm64.deb', f'XTYF-Manager-{config["version"]}-x86_64.rpm']
            else:
                names = [f'XTYF-Manager-{config["version"]}.apk']
        out.mkdir(parents=True, exist_ok=True)
        for name in names:
            (out / name).write_bytes(b'new installer ' + config['version'].encode())
            if name.endswith('.apk'):
                with zipfile.ZipFile(out / name, 'w') as apk:
                    apk.writestr('assets/frontend/asset-manifest.json', (root / 'clients/frontend/dist/asset-manifest.json').read_bytes())
                    apk.writestr('assets/frontend/index.html', b'local page')
        if out.name == 'windows':
            module.write_json(out / 'windows-verification.json', {'version':config['version'], 'framework':'tauri', 'architecture':'x64', 'installer':'NSIS', 'configurationTestsPassed':True, 'nativeExecutableVerified':True, 'clientPayloadVerified':True, 'installerPayloadExtracted':True, 'embeddedFrontendVerified':True, 'runtimeUiVerified':False, 'frontendManifestSha256': self.frontend_digest()})
        if out.name == 'linux':
            module.write_json(out / 'linux-verification.json', self.verification(config['version']))

    def frontend_digest(self):
        return hashlib.sha256((self.root / 'clients/frontend/dist/asset-manifest.json').read_bytes()).hexdigest()

    def verification(self, version):
        common = {
            'version': version, 'packageMetadataVerified': True,
            'elfArchitectureVerified': True, 'glibcRequired': '2.31',
            'elfFilesChecked': 12, 'desktopEntryVerified': True,
            'clientPayloadVerified': True, 'runtimeUiVerified': False,
            'localFrontendManifestSha256': self.frontend_digest(),
        }
        return {
            'ubuntu': {**common, 'distribution': 'Ubuntu 22.04', 'architecture': 'arm64', 'format': 'deb', 'glibcLimit': '2.35', 'dependencies': ['libc6 (>= 2.35)']},
            'fedora': {**common, 'distribution': 'Fedora 44/45', 'architecture': 'x86_64', 'format': 'rpm', 'dependencies': ['gtk3'], 'targetRuntimeStatus': 'not-tested', 'fedora45Status': 'pre-release'},
            'builder': {'environment': 'Ubuntu-22.04 WSL x86_64', 'nodeVersion': 'v24.22.0'},
        }

    def assert_rollback(self):
        for relative, content in self.original.items():
            self.assertEqual((self.root / relative).read_bytes(), content, relative)
        self.assertTrue(all(file.exists() for file in self.old))
        self.assertFalse(list(self.output.glob('.release-*')))
        self.assertFalse((self.output / '.release.lock').exists())
        for platform, pattern in module.INSTALLERS.items():
            for file in (self.output / platform).glob('*'):
                match = pattern.fullmatch(file.name)
                if match:
                    self.assertNotEqual(match[1], '1.0.10', str(file))

    def test_four_installers_bump_and_cleanup_only_after_success(self):
        unrelated = [self.output / 'windows/user-installer.exe', self.output / 'linux/user-package.deb', self.output / 'linux/user-package.rpm']
        for file in unrelated:
            file.write_bytes(b'keep')
        report = module.release(self.root, self.builder)
        self.assertEqual(report['version'], '1.0.10')
        self.assertEqual(report['android']['versionCode'], 12)
        self.assertEqual(len(self.calls), 6)
        self.assertIn('--check', self.calls[0])
        self.assertEqual(len(report['artifacts']), 4)
        self.assertEqual(report['linux'], self.verification('1.0.10'))
        self.assertEqual(len((self.output / 'SHA256SUMS.txt').read_text().splitlines()), 4)
        self.assertFalse(any(file.exists() for file in self.old))
        self.assertTrue(all(file.exists() for file in unrelated))
        for artifact in report['artifacts']:
            self.assertTrue((self.output / artifact['file']).exists())
        package = module.read_json(self.root / 'clients/desktop/package.json')
        lock = module.read_json(self.root / 'clients/desktop/package-lock.json')
        self.assertEqual(package['version'], '1.0.10')
        self.assertEqual(lock['packages']['']['version'], '1.0.10')
        manifest = module.ET.parse(self.root / 'clients/android/AndroidManifest.xml').getroot()
        self.assertEqual(manifest.get(f'{{{module.ANDROID_NS}}}versionCode'), '12')

    def test_second_build_failure_restores_version_and_old_installers(self):
        def fail_android(command, root):
            if any(Path(str(arg)).name == 'build_android.py' for arg in command):
                raise subprocess.CalledProcessError(1, command)
            self.builder(command, root)
        with self.assertRaises(subprocess.CalledProcessError):
            module.release(self.root, fail_android)
        self.assert_rollback()
        self.assertEqual(module.next_config(self.root)['version'], '1.0.10')

    def test_frontend_mismatch_rolls_back_all_packages(self):
        for platform in ('windows', 'ubuntu', 'fedora', 'android'):
            with self.subTest(platform=platform):
                def corrupt(command, root):
                    self.builder(command, root)
                    if '--output-dir' in command and Path(command[1]).name == 'build_linux.py':
                        staging = Path(command[command.index('--output-dir') + 1]).parent
                        if platform == 'android':
                            path = staging / 'android/XTYF-Manager-1.0.10.apk'
                            with zipfile.ZipFile(path, 'w') as apk:
                                apk.writestr('assets/frontend/asset-manifest.json', b'{}')
                        else:
                            path = staging / ('windows/windows-verification.json' if platform == 'windows' else 'linux/linux-verification.json')
                            value = module.read_json(path)
                            if platform == 'windows':
                                value['frontendManifestSha256'] = 'wrong'
                            else:
                                value[platform]['localFrontendManifestSha256'] = 'wrong'
                            module.write_json(path, value)
                with self.assertRaisesRegex(RuntimeError, 'frontend manifest mismatch'):
                    module.release(self.root, corrupt)
                self.assert_rollback()

    def test_linux_preflight_failure_does_not_change_version_or_start_builds(self):
        def fail_preflight(command, root):
            self.calls.append(command)
            self.assertIn('--check', command)
            raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(subprocess.CalledProcessError):
            module.release(self.root, fail_preflight)
        self.assertEqual(len(self.calls), 1)
        self.assert_rollback()

    def test_linux_build_failure_restores_versions_and_all_previous_installers(self):
        def fail_linux(command, root):
            if '--output-dir' in command and Path(command[1]).name == 'build_linux.py':
                self.calls.append(command)
                raise subprocess.CalledProcessError(1, command)
            self.builder(command, root)
        with self.assertRaises(subprocess.CalledProcessError):
            module.release(self.root, fail_linux)
        self.assertEqual(len(self.calls), 6)
        self.assert_rollback()

    def test_missing_linux_artifact_does_not_publish_partial_release(self):
        def omit_rpm(command, root):
            self.builder(command, root)
            if '--output-dir' in command and Path(command[1]).name == 'build_linux.py':
                out = Path(command[command.index('--output-dir') + 1])
                (out / 'XTYF-Manager-1.0.10-x86_64.rpm').unlink()
        with self.assertRaisesRegex(RuntimeError, 'complete installer.*rpm'):
            module.release(self.root, omit_rpm)
        self.assert_rollback()

    def test_linux_verification_must_match_release_and_pass(self):
        cases = (
            ('ubuntu', 'version', '1.0.9'),
            ('ubuntu', 'architecture', 'amd64'),
            ('fedora', 'format', 'deb'),
            ('fedora', 'packageMetadataVerified', False),
            ('ubuntu', 'elfArchitectureVerified', False),
            ('fedora', 'desktopEntryVerified', False),
            ('ubuntu', 'clientPayloadVerified', False),
            ('ubuntu', 'glibcRequired', '2.36'),
            ('ubuntu', 'glibcRequired', 'unknown'),
            ('ubuntu', 'glibcLimit', '2.36'),
        )
        for target, field, value in cases:
            with self.subTest(target=target, field=field, value=value):
                def invalid_verification(command, root):
                    self.builder(command, root)
                    if '--output-dir' in command and Path(command[1]).name == 'build_linux.py':
                        out = Path(command[command.index('--output-dir') + 1])
                        verification = self.verification('1.0.10')
                        verification[target][field] = value
                        module.write_json(out / 'linux-verification.json', verification)
                with self.assertRaisesRegex(RuntimeError, 'Linux verification'):
                    module.release(self.root, invalid_verification)
                self.assert_rollback()

    def test_missing_linux_verification_does_not_publish(self):
        def omit_verification(command, root):
            self.builder(command, root)
            if '--output-dir' in command and Path(command[1]).name == 'build_linux.py':
                out = Path(command[command.index('--output-dir') + 1])
                (out / 'linux-verification.json').unlink()
        with self.assertRaisesRegex(RuntimeError, 'verification report'):
            module.release(self.root, omit_verification)
        self.assert_rollback()

    def test_windows_payload_must_actually_be_verified(self):
        for field in ('installerPayloadExtracted', 'embeddedFrontendVerified', 'configurationTestsPassed'):
            with self.subTest(field=field):
                def invalid(command, root):
                    self.builder(command, root)
                    if '-OutputDirectory' in command:
                        out = Path(command[command.index('-OutputDirectory') + 1])
                        path = out / 'windows-verification.json'
                        result = module.read_json(path)
                        result[field] = False
                        module.write_json(path, result)
                with self.assertRaisesRegex(RuntimeError, 'Windows verification failed'):
                    module.release(self.root, invalid)
                self.assert_rollback()

    def test_missing_artifact_does_not_count_as_success(self):
        with self.assertRaisesRegex(RuntimeError, 'complete installer'):
            module.release(self.root, lambda command, root: None)
        self.assert_rollback()

    def test_report_publish_failure_rolls_back_installer_copies_and_reports(self):
        original_replace = Path.replace
        def fail_checksum(path, target):
            if path.name == 'SHA256SUMS.txt':
                raise OSError('simulated disk error')
            return original_replace(path, target)
        with patch.object(Path, 'replace', fail_checksum), self.assertRaises(OSError):
            module.release(self.root, self.builder)
        self.assert_rollback()

    def test_partial_installer_copy_is_removed_on_failure(self):
        original_copy = module.shutil.copy2
        def partial_copy(source, destination):
            if Path(destination).name == 'XTYF-Manager-1.0.10-x86_64.rpm':
                Path(destination).write_bytes(b'incomplete copy')
                raise OSError('simulated incomplete copy')
            return original_copy(source, destination)
        with patch.object(module.shutil, 'copy2', partial_copy), self.assertRaises(OSError):
            module.release(self.root, self.builder)
        self.assert_rollback()

    def test_numbering_uses_latest_release_even_if_config_was_reverted(self):
        config = {**self.config, 'version': '1.0.1', 'versionCode': 2}
        module.write_json(self.root / 'clients/config.json', config)
        self.assertEqual(module.next_config(self.root)['version'], '1.0.10')
        self.assertEqual(module.next_config(self.root)['versionCode'], 12)

    def test_numbering_includes_both_linux_installer_formats(self):
        for name in ('XTYF-Manager-1.0.20-arm64.deb', 'XTYF-Manager-1.0.20-x86_64.rpm'):
            with self.subTest(name=name):
                path = self.output / 'linux' / name
                path.write_bytes(b'newer Linux installer')
                self.assertEqual(module.next_config(self.root)['version'], '1.0.21')
                path.unlink()

    def test_temporary_cleanup_failure_does_not_undo_successful_release(self):
        with patch.object(module.shutil, 'rmtree', side_effect=PermissionError('temporary file in use')):
            report = module.release(self.root, self.builder)
        self.assertEqual(report['version'], '1.0.10')
        self.assertFalse(any(file.exists() for file in self.old))
        self.assertEqual(module.read_json(self.root / 'clients/config.json')['version'], '1.0.10')
        self.assertFalse((self.output / '.release.lock').exists())

    def test_concurrent_release_does_not_modify_files(self):
        lock = self.output / '.release.lock'
        lock.write_text('12345')
        with self.assertRaisesRegex(RuntimeError, 'release is running'):
            module.release(self.root, self.builder)
        for relative, content in self.original.items():
            self.assertEqual((self.root / relative).read_bytes(), content)
        self.assertEqual(self.calls, [])

    def test_confirmed_version_preview_still_advances_version_code(self):
        config = module.requested_config(self.root, '2.0.0')
        self.assertEqual(config['version'], '2.0.0')
        self.assertEqual(config['versionCode'], 12)

    def test_confirmed_version_must_be_newer_than_everything_released(self):
        for requested in ('1.0.9', '1.0.8', '0.9.0'):
            with self.subTest(requested=requested):
                with self.assertRaises(ValueError):
                    module.requested_config(self.root, requested)
        with self.assertRaises(ValueError):
            module.requested_config(self.root, '2.0')

    def test_release_builds_the_confirmed_version(self):
        report = module.release(self.root, self.builder, requested_version='2.0.0')
        self.assertEqual(report['version'], '2.0.0')
        self.assertEqual(report['android']['versionCode'], 12)
        self.assertTrue((self.output / 'android/XTYF-Manager-2.0.0.apk').exists())
        self.assertEqual(module.read_json(self.root / 'clients/config.json')['version'], '2.0.0')
        self.assertEqual(module.next_config(self.root)['version'], '2.0.1')


if __name__ == '__main__':
    unittest.main()
