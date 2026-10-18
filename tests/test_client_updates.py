"""Signed releases use synthetic assets, ephemeral keys and the isolated API DB."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from clients import ui_release
from modules.client_updates import validate_payload, verify_envelope
from test_tasks import app_module


class TestClientUpdates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        cls.private_bytes = cls.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption())
        cls.public_bytes = cls.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                            serialization.PublicFormat.SubjectPublicKeyInfo)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="xtyf-ui-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.private = self.root / "private.pem"
        self.public = self.root / "public.pem"
        self.private.write_bytes(self.private_bytes)
        self.public.write_bytes(self.public_bytes)
        self.frontend = self.root / "frontend"
        self.frontend.mkdir()
        self.output = self.root / "release"
        self.make_frontend("A")
        self.app = app_module.app
        changes = patch.dict(self.app.config, CLIENT_UI_RELEASE_DIR=str(self.output),
                             CLIENT_UPDATE_PUBLIC_KEY=str(self.public))
        changes.start()
        self.addCleanup(changes.stop)
        self.client = self.app.test_client()

    def make_frontend(self, version):
        files = {name: f"synthetic {version} {name}".encode() for name in
                 ("index.html", "shell.js", "bridge.js", "page-runtime.js", "templates.js")}
        for name, content in files.items():
            (self.frontend / name).write_bytes(content)
        manifest = {"protocolVersion": 1, "version": "1.0.10", "templates": [],
                    "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
                    "sizes": {k: len(v) for k, v in files.items()}}
        (self.frontend / "asset-manifest.json").write_bytes(ui_release.canonical(manifest))

    def publish(self, sequence=1, version="A", installers=None):
        return ui_release.publish(self.frontend, self.output, self.private, self.public, sequence, version, installers)

    def test_absent_release_is_explicit_and_public_before_login(self):
        response = self.client.get("/api/client/updates", headers={"X-XTYF-Client-Protocol": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"ok": True, "available": False})
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_signed_release_download_and_exact_manifest_file_totals(self):
        payload = self.publish()
        response = self.client.get("/api/client/updates", headers={"X-XTYF-Client-Protocol": "1"})
        self.assertEqual(response.status_code, 200)
        _, verified = verify_envelope(response.data, self.public_bytes)
        self.assertEqual(verified, payload)
        download = self.client.get(payload["bundle"]["url"], headers={"X-XTYF-Client-Protocol": "1"})
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.mimetype, "application/zip")
        self.assertEqual(hashlib.sha256(download.data).hexdigest(), payload["bundle"]["sha256"])
        download.close()
        with zipfile.ZipFile(self.output / "bundles" / (payload["bundle"]["id"] + ".zip")) as archive:
            self.assertEqual(len(archive.infolist()), payload["bundle"]["fileCount"])
            self.assertEqual(sum(i.file_size for i in archive.infolist()), payload["bundle"]["unpackedSize"])
            self.assertNotIn("private.pem", archive.namelist())

    def test_payload_tampering_rejected_by_signature(self):
        self.publish()
        envelope = json.loads((self.output / "update.json").read_bytes())
        payload = json.loads(base64.b64decode(envelope["payload"]))
        payload["sequence"] = 99
        envelope["payload"] = base64.b64encode(ui_release.canonical(payload)).decode()
        with self.assertRaises(InvalidSignature):
            verify_envelope(ui_release.canonical(envelope), self.public_bytes)

    def test_wrong_key_and_malformed_metadata_never_served(self):
        self.publish()
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        self.public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                             serialization.PublicFormat.SubjectPublicKeyInfo))
        self.assertEqual(self.client.get("/api/client/updates").status_code, 503)
        (self.output / "update.json").write_bytes(b"not JSON")
        self.assertEqual(self.client.get("/api/client/updates").status_code, 503)

    def test_incomplete_bundle_is_not_advertised(self):
        payload = self.publish()
        (self.output / "bundles" / (payload["bundle"]["id"] + ".zip")).unlink()
        self.assertEqual(self.client.get("/api/client/updates").status_code, 503)

    def test_corrupt_bundle_is_not_downloaded(self):
        payload = self.publish()
        path = self.output / "bundles" / (payload["bundle"]["id"] + ".zip")
        original = path.read_bytes()
        path.write_bytes(b"X" + original[1:])
        self.assertEqual(self.client.get(payload["bundle"]["url"]).status_code, 503)

    def test_unknown_bundle_and_path_traversal_return_not_found(self):
        self.publish()
        for name in ("nothex", "0" * 64, "..%2Fprivate.pem"):
            self.assertEqual(self.client.get("/api/client/ui/" + name + ".zip").status_code, 404)

    def test_first_password_gate_allows_updates_but_not_business(self):
        self.publish()
        admin = self.app.test_client()
        self.assertEqual(admin.post("/login", json={"username": "admin", "password": "123456"}).status_code, 200)
        import uuid
        username = "update_" + uuid.uuid4().hex[:12]
        created = admin.post("/api/member", json={"name": username, "username": username, "password": "test-pass-123"})
        self.assertEqual(created.status_code, 200)
        from flask.testing import FlaskClient
        client = FlaskClient(self.app, use_cookies=True)
        headers = {"X-XTYF-Client-Protocol": "1"}
        token = client.get("/api/client/bootstrap", headers=headers).json["csrf_token"]
        response = client.post("/api/client/login", json={"username": username, "password": "test-pass-123"},
                               headers={**headers, "X-CSRF-Token": token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/api/client/updates", headers=headers).status_code, 200)
        self.assertEqual(client.get("/api/data", headers=headers).status_code, 403)

    def test_bootstrap_exposes_compatibility_without_changing_protocol(self):
        payload = self.client.get("/api/client/bootstrap").json
        self.assertEqual(payload["protocolVersion"], 1)
        self.assertEqual(payload["uiCompatibility"], {"protocolMin": 1, "protocolMax": 1})
        self.client.post("/login", json={"username": "admin", "password": "123456"})
        self.assertIn("avatar_url", self.client.get("/api/client/bootstrap").json["user"])

    def test_invalid_limits_protocol_and_links_rejected(self):
        payload = self.publish()
        cases = [("size", 128 * 1024 * 1024 + 1), ("unpackedSize", -1), ("fileCount", True),
                 ("protocolMin", 2), ("id", "../bad"), ("url", "https://untrusted.test/ui.zip"),
                 ("minClientVersion", "latest"), ("capabilities", [3])]
        for field, value in cases:
            with self.subTest(field=field):
                bad = json.loads(json.dumps(payload))
                bad["bundle"][field] = value
                with self.assertRaises(ValueError):
                    validate_payload(bad)
        for value in (True, -1, 2**53):
            with self.assertRaises(ValueError):
                validate_payload({**payload, "sequence": value})
        for url in ("//evil.test/a.exe", "https://evil.test/a.exe", "/../key", "/a\\b", "/a#x"):
            with self.assertRaises(ValueError):
                validate_payload({**payload, "installers": {"android": {"version": "1.0.11", "url": url, "sha256": "a" * 64}}})

    def test_same_assets_repeat_without_zip_changes_and_rollback_new_sequence(self):
        a = self.publish()
        again = self.publish(sequence=2)
        self.assertEqual(a["bundle"], again["bundle"])
        self.make_frontend("B")
        b = self.publish(sequence=3, version="B")
        self.assertNotEqual(a["bundle"]["id"], b["bundle"]["id"])
        self.make_frontend("A")
        rollback = self.publish(sequence=4, version="A")
        self.assertEqual(a["bundle"], rollback["bundle"])

    def test_bad_asset_or_escape_rejected_before_publishing(self):
        self.publish()
        old = (self.output / "update.json").read_bytes()
        (self.frontend / "index.html").write_bytes(b"changed after build")
        with self.assertRaises(ValueError):
            self.publish(2, "B")
        self.assertEqual(old, (self.output / "update.json").read_bytes())
        manifest_path = self.frontend / "asset-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        manifest["files"]["../private.pem"] = hashlib.sha256(self.private_bytes).hexdigest()
        manifest["sizes"]["../private.pem"] = len(self.private_bytes)
        manifest_path.write_bytes(ui_release.canonical(manifest))
        with self.assertRaises(ValueError):
            self.publish(2, "B")

    def test_atomic_manifest_failure_preserves_previous_release(self):
        self.publish()
        old = (self.output / "update.json").read_bytes()
        self.make_frontend("B")
        replace = ui_release.os.replace
        def fail_manifest(source, target):
            if Path(target).name == "update.json":
                raise OSError("simulated disk failure")
            return replace(source, target)
        with patch.object(ui_release.os, "replace", side_effect=fail_manifest):
            with self.assertRaises(OSError):
                self.publish(2, "B")
        self.assertEqual(old, (self.output / "update.json").read_bytes())
        self.assertEqual(self.client.get("/api/client/updates").status_code, 200)

    def test_sequence_is_monotonic_and_publisher_lock_is_exclusive(self):
        self.assertEqual(ui_release.reserve_sequence(self.private, 5), 5)
        with self.assertRaises(ValueError):
            ui_release.reserve_sequence(self.private, 5)
        self.assertGreater(ui_release.reserve_sequence(self.private), 5)
        lock = self.private.with_suffix(".lock")
        lock.touch()
        with self.assertRaises(FileExistsError):
            ui_release.reserve_sequence(self.private)


if __name__ == "__main__":
    unittest.main()
