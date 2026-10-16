"""Increment the shared version, build four installers, then replace old releases locally."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILES = (
    'clients/config.json', 'clients/windows/config.json',
    'clients/windows/package.json', 'clients/windows/package-lock.json',
    'clients/android/AndroidManifest.xml',
)
INSTALLERS = {
    'windows': re.compile(r'XTYF-Manager-Setup-(\d+\.\d+\.\d+)-x64\.exe(?:\.blockmap)?'),
    'android': re.compile(r'XTYF-Manager-(\d+\.\d+\.\d+)\.apk'),
    'linux': re.compile(r'XTYF-Manager-(\d+\.\d+\.\d+)-(?:arm64\.deb|x86_64\.rpm)'),
}
ANDROID_NS = 'http://schemas.android.com/apk/res/android'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def version_parts(version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError(f'Invalid release version: {version!r}; expected major.minor.patch')
    return tuple(map(int, version.split('.')))


def next_config(root):
    config = read_json(root / 'clients/config.json')
    versions = [version_parts(config['version'])]
    code = int(config['versionCode'])
    output = root / 'dist/clients'
    report_path = output / 'build-report.json'
    if report_path.exists():
        report = read_json(report_path)
        versions.append(version_parts(report['version']))
        code = max(code, int(report.get('android', {}).get('versionCode', 0)))
    for platform, pattern in INSTALLERS.items():
        for file in (output / platform).glob('*'):
            match = pattern.fullmatch(file.name)
            if match:
                versions.append(version_parts(match[1]))
    major, minor, patch = max(versions)
    if not 0 < code < 2100000000:
        raise ValueError('Android versionCode is outside the incrementable range')
    return {**config, 'version': f'{major}.{minor}.{patch + 1}', 'versionCode': code + 1}


def sync_versions(root, config):
    for relative in ('clients/config.json', 'clients/windows/config.json'):
        write_json(root / relative, config)
    for relative in ('clients/windows/package.json', 'clients/windows/package-lock.json'):
        data = read_json(root / relative)
        data['version'] = config['version']
        if 'packages' in data:
            data['packages']['']['version'] = config['version']
        write_json(root / relative, data)
    ET.register_namespace('android', ANDROID_NS)
    path = root / 'clients/android/AndroidManifest.xml'
    manifest = ET.parse(path)
    manifest.getroot().set(f'{{{ANDROID_NS}}}versionName', config['version'])
    manifest.getroot().set(f'{{{ANDROID_NS}}}versionCode', str(config['versionCode']))
    manifest.write(path, encoding='utf-8', xml_declaration=True)


@contextmanager
def release_lock(output):
    lock = output / '.release.lock'
    try:
        stream = lock.open('x', encoding='ascii')
    except FileExistsError:
        raise RuntimeError(f'A release is running. If it was interrupted, confirm it stopped before removing {lock}') from None
    try:
        with stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink()


def run_command(command, root):
    subprocess.run([str(arg) for arg in command], cwd=root, check=True)


def remove_old_installers(output, version):
    removed = []
    for platform, pattern in INSTALLERS.items():
        directory = (output / platform).resolve()
        for path in directory.glob('*'):
            match = pattern.fullmatch(path.name)
            if match and version_parts(match[1]) < version_parts(version):
                if path.is_symlink() or path.resolve().parent != directory:
                    raise RuntimeError(f'Unexpected installer path: {path}')
                path.unlink()
                removed.append(str(path.relative_to(output)).replace('\\', '/'))
    return removed


def linux_verification(staging, version):
    path = staging / 'linux/linux-verification.json'
    if not path.is_file():
        raise RuntimeError('Linux builder did not produce its verification report')
    verification = read_json(path)
    for target, architecture, package_format in (
        ('ubuntu', 'arm64', 'deb'), ('fedora', 'x86_64', 'rpm'),
    ):
        result = verification.get(target, {})
        expected = {'version': version, 'architecture': architecture, 'format': package_format}
        for field, value in expected.items():
            if result.get(field) != value:
                raise RuntimeError(f'Linux verification mismatch: {target}.{field}; expected {value!r}')
        for field in ('packageMetadataVerified', 'elfArchitectureVerified', 'desktopEntryVerified', 'clientPayloadVerified'):
            if result.get(field) is not True:
                raise RuntimeError(f'Linux verification failed: {target}.{field}')
    ubuntu = verification['ubuntu']
    required = ubuntu.get('glibcRequired', '')
    if not isinstance(required, str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', required):
        raise RuntimeError('Linux verification has no valid Ubuntu GLIBC requirement')
    required_parts = tuple(map(int, required.split('.')))
    required_parts += (0,) * (3 - len(required_parts))
    if ubuntu.get('glibcLimit') != '2.35' or required_parts > (2, 35, 0):
        raise RuntimeError('Linux verification exceeds Ubuntu 22.04 GLIBC 2.35 compatibility')
    return verification


def release(root=ROOT, runner=run_command):
    root = Path(root).resolve()
    output = (root / 'dist/clients').resolve()
    if not output.is_relative_to(root):
        raise RuntimeError('Client release output must stay inside the project')
    output.mkdir(parents=True, exist_ok=True)
    with release_lock(output):
        config = next_config(root)
        version = config['version']
        # Fail on missing Linux tools before changing versions or building other platforms.
        runner([sys.executable, root / 'clients/build_linux.py', '--check'], root)
        rollback_files = (*VERSION_FILES, 'dist/clients/build-report.json', 'dist/clients/SHA256SUMS.txt')
        snapshots = {relative: (root / relative).read_bytes() if (root / relative).exists() else None for relative in rollback_files}
        staging = Path(tempfile.mkdtemp(prefix='.release-', dir=output)).resolve()
        published = []
        try:
            sync_versions(root, config)
            print(f'Building version {version}, Android versionCode {config["versionCode"]}', flush=True)
            powershell = shutil.which('pwsh') or shutil.which('powershell') or 'powershell'
            runner([powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', root / 'clients/build_windows.ps1', '-OutputDirectory', staging / 'windows'], root)
            runner([sys.executable, root / 'clients/build_android.py', '--output-dir', staging / 'android'], root)
            runner([sys.executable, root / 'clients/build_linux.py', '--output-dir', staging / 'linux'], root)
            names = [
                f'windows/XTYF-Manager-Setup-{version}-x64.exe',
                f'android/XTYF-Manager-{version}.apk',
                f'linux/XTYF-Manager-{version}-arm64.deb',
                f'linux/XTYF-Manager-{version}-x86_64.rpm',
            ]
            artifacts = []
            for relative in names:
                source = staging / relative
                if not source.is_file() or source.stat().st_size == 0:
                    raise RuntimeError(f'Builder did not produce a complete installer: {relative}')
                artifacts.append({'file': relative, 'bytes': source.stat().st_size, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
            report = {
                'createdAt': datetime.now(timezone.utc).isoformat(),
                'version': version, 'serverUrl': config['serverUrl'], 'artifacts': artifacts,
                'windows': {'architecture': 'x64', 'installer': 'NSIS', 'codeSigned': False, 'configurationTestsPassed': True, 'runtimeUiVerified': False},
                'android': {'package': 'online.skywalker.manager', 'versionCode': config['versionCode'], 'signatureAndAlignmentVerified': True, 'runtimeUiVerified': False},
                'linux': linux_verification(staging, version),
                'githubPushed': False,
            }
            write_json(staging / 'build-report.json', report)
            (staging / 'SHA256SUMS.txt').write_text(''.join(a['sha256'] + '  ' + a['file'] + '\n' for a in artifacts), encoding='utf-8')
            # All four installers and their validation passed before any is published.
            blockmap = names[0] + '.blockmap'
            for relative in names + ([blockmap] if (staging / blockmap).is_file() else []):
                destination = output / relative
                if destination.exists():
                    raise RuntimeError(f'Refusing to overwrite an existing release: {destination}')
                destination.parent.mkdir(parents=True, exist_ok=True)
                published.append(destination)
                shutil.copy2(staging / relative, destination)
            # Reports are replaced only after all four installer copies succeed.
            for name in ('build-report.json', 'SHA256SUMS.txt'):
                (staging / name).replace(output / name)
        except BaseException:
            for relative, content in snapshots.items():
                path = root / relative
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
            for path in published:
                path.unlink(missing_ok=True)
            print('Release failed; previous version and installers were preserved.', file=sys.stderr)
            raise
        finally:
            if staging.parent != output.resolve() or not staging.name.startswith('.release-'):
                raise RuntimeError(f'Unexpected staging directory: {staging}')
            try:
                shutil.rmtree(staging)
            except OSError as exc:
                print(f'Temporary build cleanup needs attention: {staging}: {exc}', file=sys.stderr)
        # Keep the successful new release if optional documentation/cleanup fails.
        try:
            shutil.copy2(root / 'clients/README.md', output / '安装与构建说明.md')
            removed = remove_old_installers(output, version)
        except OSError as exc:
            print(f'Release succeeded, but cleanup needs attention: {exc}', file=sys.stderr)
            removed = []
        print(f'RELEASE_OK {version} (Android {config["versionCode"]})', flush=True)
        for artifact in artifacts:
            print(output / artifact['file'])
        if removed:
            print('Removed old installers: ' + ', '.join(removed))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Preview the next version without writing files or building')
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps(next_config(ROOT), ensure_ascii=False, indent=2))
    else:
        release()


if __name__ == '__main__':
    main()
