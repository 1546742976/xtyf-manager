#!/usr/bin/env python3
"""Package HEAD and its signed public UI; never include working-tree business data."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def build_ui_from_head(files, commit, private_key, installers=None):
    """Execute the archived builder so website and UI always represent one commit."""
    with tempfile.TemporaryDirectory(prefix='xtyf-release-head-') as temporary:
        source = Path(temporary)
        for name, content in files.items():
            target = source / name
            if not target.resolve().is_relative_to(source) or '\\' in name:
                raise RuntimeError('Unsafe source archive path: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        command = [sys.executable, str(source / 'clients/ui_release.py'),
                   '--private-key', str(private_key.resolve()), '--source-version', commit,
                   '--output-dir', str(source / 'client-ui-release')]
        if installers:
            command.extend(['--installers', str(installers.resolve())])
        subprocess.run(command, cwd=source, check=True)
        return {path.relative_to(source).as_posix(): path.read_bytes()
                for path in (source / 'client-ui-release').rglob('*') if path.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-signing-key', type=Path,
                        default=Path(os.environ.get('XTYF_UI_SIGNING_KEY', ROOT / 'clients/signing/ui-update-private.pem')))
    parser.add_argument('--installers', type=Path, help='Reviewed map of already published installer paths and hashes')
    args = parser.parse_args()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    raw = subprocess.check_output(['git', 'archive', '--format=tar', commit], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
        members = source.getmembers()
        files = {}
        for item in members:
            if item.isdir():
                continue
            if not item.isfile():
                raise RuntimeError('Release cannot contain links: ' + item.name)
            parts = Path(item.name).parts
            if any(p in ('data', 'venv', '.venv', '.local-test-data', 'backups', 'dist', 'signing', 'client-ui-release') for p in parts) or Path(item.name).name in ('secret.key', 'initial-admin-password.txt', '.env', 'release-manifest.json') or Path(item.name).name.startswith('.env.') or item.name.lower().endswith(('.db', '.db-wal', '.db-shm', '.db-journal', '.sqlite', '.sqlite3')):
                raise RuntimeError('Runtime data or reserved name tracked by Git: ' + item.name)
            files[item.name] = source.extractfile(item).read()
    required = ('deploy/update_release.py', 'deploy/consistent_snapshot.py', 'app.py', 'requirements.txt',
                'clients/ui_release.py', 'clients/update-public-key.pem')
    if any(name not in files for name in required):
        raise RuntimeError('HEAD does not contain the release scripts yet. Commit them before packaging.')
    files.update(build_ui_from_head(files, commit, args.ui_signing_key, args.installers))
    manifest = {'format': 1, 'commit': commit,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    archive = output / ('xtyf-manager-' + commit[:12] + '.tar.gz')
    prefix = archive.name.removesuffix('.tar.gz')
    with archive.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as tar:
        for name, data in {**files, 'release-manifest.json': json.dumps(manifest, indent=2).encode()}.items():
            info = tarfile.TarInfo(prefix + '/' + name)
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + '.sha256').write_text(checksum + '  ' + archive.name + '\n', encoding='ascii')
    print('Commit: ' + commit)
    print('Package: ' + str(archive))
    print('SHA256: ' + checksum)
    print('Only HEAD source and its signed UI were packaged; private key and business data were excluded.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
