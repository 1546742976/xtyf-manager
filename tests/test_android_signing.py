"""Signing safety uses temporary identities and simulated SDK output, never APK builds."""
import tempfile
import unittest
from pathlib import Path

from clients import build_android


class AndroidSigningTests(unittest.TestCase):
    def test_missing_identity_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "签名"):
                build_android.resolve_signing_identity(root, allow_create=False)
            self.assertEqual(list(root.iterdir()), [])

    def test_partial_identity_cannot_be_initialized_over(self):
        for filename in ('xtyf-manager.jks', 'android-password.txt'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / filename).write_bytes(b'existing')
                with self.assertRaises(RuntimeError):
                    build_android.resolve_signing_identity(root, allow_create=True)
                self.assertEqual((root / filename).read_bytes(), b'existing')
                self.assertEqual(len(list(root.iterdir())), 1)

    def test_existing_identity_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'xtyf-manager.jks').write_bytes(b'existing key')
            (root / 'android-password.txt').write_text('example-password', encoding='ascii')
            key, password = build_android.resolve_signing_identity(root, allow_create=False)
            self.assertEqual(key.read_bytes(), b'existing key')
            self.assertEqual(password.read_text(), 'example-password')

    def test_certificate_output_must_identify_exactly_one_signer(self):
        fingerprint = 'ab' * 32
        text = 'Verifies\nSigner #1 certificate SHA-256 digest: ' + fingerprint
        self.assertEqual(build_android.parse_certificate_sha256(text), fingerprint)
        for invalid in ('', 'Signer #1 certificate SHA-256 digest: BAD',
                        text + '\nSigner #2 certificate SHA-256 digest: ' + fingerprint):
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                build_android.parse_certificate_sha256(invalid)
