"""Opt-in real HEAD source packaging smoke; uses only a disposable signing key."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]


def extract(raw, destination):
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()) or (not member.isfile() and not member.isdir()):
                raise ValueError('Unsafe archive member')
            if member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())


def main():
    actual_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    raw = subprocess.check_output(['git', '-c', 'core.autocrlf=false', 'archive', 'HEAD'], cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix='xtyf-real-package-test-') as temporary:
        temporary = Path(temporary).resolve()
        source = temporary / 'source'; source.mkdir()
        extract(raw, source)
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        private = temporary / 'test-only.pem'
        private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        # The only source substitution is a disposable public trust anchor.
        (source / 'clients/update-public-key.pem').write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        for arguments in [('init', '-q'), ('-c', 'core.autocrlf=false', 'add', '-f', '.'),
                          ('-c', 'user.name=Package Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'Disposable packaging fixture')]:
            subprocess.run(['git', *arguments], cwd=source, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        subprocess.run([sys.executable, 'deploy/package_release.py', '--ui-signing-key', str(private)], cwd=source, check=True)
        package = next((source / 'dist').glob('*.tar.gz'))
        unpacked = temporary / 'unpacked'; unpacked.mkdir(); extract(package.read_bytes(), unpacked)
        root = next(unpacked.iterdir())
        manifest = json.loads((root / 'release-manifest.json').read_text())['files']
        lock = json.loads((root / 'clients/frontend/vendor-lock.json').read_text())['files']
        resources = {name: digest for name, digest in lock.items() if name.startswith('pdfjs/')}
        assert 'pdfjs/pdf.mjs' in resources and 'pdfjs/pdf.worker.mjs' in resources
        for kind in ('cmaps/', 'standard_fonts/', 'wasm/'):
            assert any(name.startswith('pdfjs/'+kind) for name in resources), kind
        for name, digest in resources.items():
            relative = 'clients/frontend/vendor/'+name
            assert manifest[relative] == digest
            assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest
        probe = temporary / 'probe.py'
        probe.write_text('''import sys, json, hashlib
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import app
root=Path(sys.argv[1]); client=app.app.test_client()
resources=json.loads((root/'clients/frontend/vendor-lock.json').read_text())['files']
for name,digest in resources.items():
    if not name.startswith('pdfjs/'): continue
    response=client.get('/vendor/'+name)
    assert response.status_code==200,(name,response.status_code)
    assert hashlib.sha256(response.data).hexdigest()==digest,name
assert client.get('/vendor/pdfjs/../bridge.js').status_code==404
print('EXTRACTED_OFFLINE_PDF_ROUTES_OK')
''', encoding='utf-8')
        env = dict(os.environ, PROGRESS_DB=str(temporary/'data.db'), FUNDS_DIR=str(temporary/'funds'),
                   INVOICES_DIR=str(temporary/'invoices'), TASK_ATTACHMENTS_DIR=str(temporary/'attachments'),
                   PROGRESS_SECRET_PATH=str(temporary/'secret.key'), PROGRESS_INITIAL_ADMIN_PASSWORD='synthetic-package-password', MAIL_MODE='preview')
        subprocess.run([sys.executable, str(probe), str(root)], cwd=root, env=env, check=True)
        print(json.dumps({'source_head': actual_head, 'pdf_resources': len(resources), 'package_sha256': hashlib.sha256(package.read_bytes()).hexdigest(), 'disposable_public_key': True, 'offline_routes': 'passed'}))


if __name__ == '__main__':
    main()
