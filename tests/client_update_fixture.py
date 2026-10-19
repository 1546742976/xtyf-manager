"""Make cross-language test releases with the real publisher and an ephemeral key.

Usage: python tests/client_update_fixture.py <ignored-output-directory>
No production key, business database, installer or deployment is involved.
"""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from clients.build_frontend import build  # noqa: E402
from clients.ui_release import publish  # noqa: E402


def generate(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public = destination / "public.pem"
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                    serialization.PublicFormat.SubjectPublicKeyInfo))
    with tempfile.TemporaryDirectory(prefix="xtyf-cross-language-") as temporary:
        root = Path(temporary)
        private = root / "test-private.pem"
        private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        frontend = root / "frontend"
        build(frontend)
        result = publish(frontend, destination, private, public, 100, "synthetic-source-commit-A")
        print(f'Test UI bundle: {result["bundle"]["id"]}, {result["bundle"]["fileCount"]} files')


if __name__ == "__main__":
    generate(sys.argv[1])
