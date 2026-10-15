#!/usr/bin/env python3
"""Package HEAD only; no arguments, third-party dependencies or working-tree data."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def main():
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
            if any(p in ('data', 'venv', '.venv', '.local-test-data', 'backups', 'dist') for p in parts) or Path(item.name).name in ('secret.key', 'initial-admin-password.txt', '.env', 'release-manifest.json') or Path(item.name).name.startswith('.env.') or item.name.lower().endswith(('.db', '.db-wal', '.db-shm', '.db-journal', '.sqlite', '.sqlite3')):
                raise RuntimeError('Runtime data or reserved name tracked by Git: ' + item.name)
            files[item.name] = source.extractfile(item).read()
    required = ('deploy/update_release.py', 'deploy/consistent_snapshot.py', 'app.py', 'requirements.txt')
    if any(name not in files for name in required):
        raise RuntimeError('HEAD does not contain the release scripts yet. Commit them before packaging.')
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
    print('Only HEAD was packaged; uncommitted and untracked files were excluded.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
