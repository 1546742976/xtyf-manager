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

if __package__:
    from .site_updates_snapshot import create_snapshot
else:
    from site_updates_snapshot import create_snapshot

ROOT = Path(__file__).resolve().parents[1]
PWA_REQUIRED_FILES = (
    'modules/pwa.py', 'templates/_pwa_head.html', 'templates/_pwa_install.html',
    'static/pwa.js', 'static/pwa.css', 'static/pwa/sw.js', 'static/pwa/offline.html',
    'static/pwa/icon-192.png', 'static/pwa/icon-512.png',
    'static/pwa/maskable-512.png', 'static/pwa/apple-touch-icon.png',
)


def archive_head(commit):
    # git archive honors core.autocrlf. Preserve locked vendor bytes even when
    # the publisher normally checks out Windows CRLF working files.
    return subprocess.check_output(['git', '-c', 'core.autocrlf=false', 'archive', '--format=tar', commit], cwd=ROOT)


def build_ui_from_head(files, commit, private_key, installers=None):
    """Execute the archived builder so website and UI always represent one commit."""
    with tempfile.TemporaryDirectory(prefix='xtyf-release-head-') as temporary:
        source = Path(temporary).resolve()
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
        generated = {path.relative_to(source).as_posix(): path.read_bytes()
                     for path in (source / 'client-ui-release').rglob('*') if path.is_file()}
        # The website serves the same pinned PDF.js resources as the local UI.
        # Copy only entries in HEAD's lock, not the build environment or cache.
        lock = source / 'clients/frontend/vendor-lock.json'
        if lock.is_file():
            vendor = source / 'clients/frontend/vendor'
            for name, digest in json.loads(lock.read_text(encoding='utf-8'))['files'].items():
                if not name.startswith('pdfjs/'):
                    continue
                resource = (vendor / name).resolve()
                if not resource.is_relative_to((vendor / 'pdfjs').resolve()) or not resource.is_file():
                    raise RuntimeError('Missing or unsafe PDF resource: ' + name)
                content = resource.read_bytes()
                if hashlib.sha256(content).hexdigest() != digest:
                    raise RuntimeError('Invalid PDF resource checksum: ' + name)
                generated['clients/frontend/vendor/' + name] = content
        return generated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-signing-key', type=Path,
                        default=Path(os.environ.get('XTYF_UI_SIGNING_KEY', ROOT / 'clients/signing/ui-update-private.pem')))
    parser.add_argument('--installers', type=Path, help='Reviewed map of already published installer paths and hashes')
    args = parser.parse_args()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    raw = archive_head(commit)
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
        members = source.getmembers()
        files = {}
        for item in members:
            if item.isdir():
                continue
            if not item.isfile():
                raise RuntimeError('Release cannot contain links: ' + item.name)
            parts = Path(item.name).parts
            if any(p in ('data', 'venv', '.venv', '.local-test-data', 'backups', 'dist', 'signing', 'client-ui-release') for p in parts) or Path(item.name).name in ('secret.key', 'initial-admin-password.txt', '.env', 'release-manifest.json', 'site-release.json') or Path(item.name).name.startswith('.env.') or item.name.lower().endswith(('.db', '.db-wal', '.db-shm', '.db-journal', '.sqlite', '.sqlite3')):
                raise RuntimeError('Runtime data or reserved name tracked by Git: ' + item.name)
            files[item.name] = source.extractfile(item).read()
    required = ('deploy/update_release.py', 'deploy/consistent_snapshot.py', 'app.py', 'requirements.txt',
                'clients/ui_release.py', 'clients/update-public-key.pem') + PWA_REQUIRED_FILES
    if any(name not in files for name in required):
        raise RuntimeError('HEAD does not contain the release scripts yet. Commit them before packaging.')
    files.update(build_ui_from_head(files, commit, args.ui_signing_key, args.installers))
    # Snapshot Git log on the packaging machine: production needs no .git or GitHub.
    files['site-release.json'] = create_snapshot(ROOT, commit)
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
