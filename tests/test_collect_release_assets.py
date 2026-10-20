"""Regression tests for GitHub Release installer collection."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from clients.collect_release_assets import collect, expected_artifacts, validate_tag


class ReleaseCollectionTests(unittest.TestCase):
    def test_tag_rules(self):
        validate_tag("v1.1.0", "stable", "1.1.0")
        validate_tag("20261020-beta143", "prerelease", "1.1.0")
        for tag, channel in (("v1.1.1", "stable"), ("20261345-beta1", "prerelease"),
                             ("20261020-beta0", "prerelease"), ("v1.1.0", "prerelease")):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                validate_tag(tag, channel, "1.1.0")

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.artifacts = self.root / "artifacts"
        for artifact, filenames in expected_artifacts("1.1.0").items():
            folder = self.artifacts / artifact
            folder.mkdir(parents=True)
            entries = []
            for filename in filenames:
                content = (artifact + "/" + filename).encode("ascii") * 50
                (folder / filename).write_bytes(content)
                entries.append(f"{hashlib.sha256(content).hexdigest()}  {filename}\n")
            (folder / "SHA256SUMS.txt").write_text("".join(entries), encoding="ascii")

    def test_valid_stable_release(self):
        output = self.root / "out"
        records = collect(self.artifacts, output, version="1.1.0", tag="v1.1.0", channel="stable")
        self.assertEqual(len(records), 7)
        self.assertEqual(len((output / "SHA256SUMS.txt").read_text().splitlines()), 7)
        report = json.loads((output / "build-report.json").read_text())
        self.assertEqual(report["tag"], "v1.1.0")
        self.assertEqual(report["channel"], "stable")

    def test_mismatched_binary_rejected(self):
        target = self.artifacts / "xtyf-client-windows-x64" / "XTYF-Manager-Setup-1.1.0-x64.exe"
        target.write_bytes(target.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            collect(self.artifacts, self.root / "out", version="1.1.0",
                    tag="20261020-beta1", channel="prerelease")

    def test_missing_platform_rejected(self):
        import shutil
        shutil.rmtree(self.artifacts / "xtyf-client-linux-arm64")
        with self.assertRaisesRegex(ValueError, "Missing or unexpected"):
            collect(self.artifacts, self.root / "out", version="1.1.0",
                    tag="v1.1.0", channel="stable")

    def test_wrong_version_rejected(self):
        with self.assertRaises(ValueError):
            collect(self.artifacts, self.root / "out", version="1.1.1",
                    tag="v1.1.1", channel="stable")

    def test_unknown_checksum_rejected(self):
        manifest = self.artifacts / "xtyf-client-windows-x64" / "SHA256SUMS.txt"
        manifest.write_text(manifest.read_text() + "0" * 64 + "  hidden.exe\n")
        with self.assertRaisesRegex(ValueError, "Unexpected files"):
            collect(self.artifacts, self.root / "out", version="1.1.0",
                    tag="v1.1.0", channel="stable")


if __name__ == "__main__":
    unittest.main()
