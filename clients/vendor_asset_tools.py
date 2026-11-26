"""Fetch only pinned, hash-verified public barcode/PDF browser assets (maintainer tool)."""

import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "@zxing/library": ("0.21.3", "package/umd/index.min.js", "zxing.js"),
    "jsbarcode": ("3.12.1", "package/dist/barcodes/JsBarcode.code128.min.js", "barcode.js"),
}


def main():
    target = ROOT / "static/vendor/asset-tools"
    target.mkdir(parents=True, exist_ok=True)
    lock = {}
    for package, (version, source, filename) in PACKAGES.items():
        with urllib.request.urlopen(f"https://registry.npmjs.org/{package}/{version}", timeout=60) as r:
            meta = json.load(r)
        with urllib.request.urlopen(meta["dist"]["tarball"], timeout=60) as r:
            data = r.read()
        integrity = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
        if integrity != meta["dist"]["integrity"]:
            raise ValueError("Integrity mismatch: " + package)
        entry = {
            "version": version,
            "source": meta["dist"]["tarball"],
            "integrity": integrity,
            "license": meta.get("license"),
            "files": {},
        }
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            names = [source] + [
                n
                for n in archive.getnames()
                if n.lower()
                in ("package/license", "package/license.txt", "package/license.md", "package/mit-license.txt")
            ]
            if len(names) < 2:
                raise ValueError("Missing license: " + package)
            for name in names:
                content = archive.extractfile(name).read()
                dest = filename if name == source else filename + ".LICENSE"
                (target / dest).write_bytes(content)
                entry["files"][dest] = hashlib.sha256(content).hexdigest()
        lock[package] = entry
    (target / "lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
