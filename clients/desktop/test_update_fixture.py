"""Synthetic signed real-front-end fixtures for the Windows native runtime check."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from clients.ui_release import publish  # noqa: E402


def generate(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public = destination / "public.pem"
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                    serialization.PublicFormat.SubjectPublicKeyInfo))
    with tempfile.TemporaryDirectory(prefix="xtyf-native-update-fixture-") as temporary:
        temporary = Path(temporary)
        private = temporary / "private.pem"
        private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        for sequence, label in enumerate(("A", "B", "broken-B"), 100):
            frontend = temporary / label
            shutil.copytree(ROOT / "clients/frontend/dist", frontend)
            index = frontend / "index.html"
            if label == "broken-B":
                index.write_text('<!doctype html><html><head><title>Intentionally broken test UI</title></head>'
                                 '<body data-update-fixture="broken-B">Synthetic startup failure</body></html>',
                                 encoding="utf-8")
            else:
                index.write_text(index.read_text(encoding="utf-8").replace("<body>", f'<body data-update-fixture="{label}">'),
                                 encoding="utf-8")
            manifest_path = frontend / "asset-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data = index.read_bytes()
            manifest["files"]["index.html"] = hashlib.sha256(data).hexdigest()
            manifest["sizes"]["index.html"] = len(data)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            publish(frontend, destination / label, private, public, sequence, f"native-smoke-{label}")
    print("Native runtime fixtures generated; synthetic private key was ephemeral")


if __name__ == "__main__":
    generate(sys.argv[1])
