"""Build a signed public UI release; never build an installer or bump its version."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from clients.build_frontend import build  # noqa: E402
from modules.client_updates import MAX_FILES, MAX_METADATA, MAX_UNPACKED, MAX_ZIP, validate_payload  # noqa: E402


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ui-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reserve_sequence(private_path, requested=None):
    """Publisher-local monotonically increasing sequence, including rollback releases."""
    record = Path(private_path).with_suffix(".sequence")
    lock = record.with_suffix(".lock")
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        previous = int(record.read_text()) if record.exists() else 0
        sequence = requested if requested is not None else max(previous + 1, time.time_ns() // 1_000_000)
        if type(sequence) is not int or not previous < sequence < 2**53:
            raise ValueError("Release sequence must be greater than the previous publication")
        atomic_write(record, str(sequence).encode("ascii"))
        return sequence
    finally:
        os.close(fd)
        lock.unlink()


def package_frontend(frontend, target):
    frontend, target = Path(frontend), Path(target)
    manifest_bytes = (frontend / "asset-manifest.json").read_bytes()
    if len(manifest_bytes) > MAX_METADATA:
        raise ValueError("Frontend manifest too large")
    manifest = json.loads(manifest_bytes)
    files = manifest["files"]
    sizes = manifest["sizes"]
    if set(sizes) != set(files) or not {"index.html", "shell.js", "bridge.js", "page-runtime.js", "templates.js"} <= set(files):
        raise ValueError("Incomplete frontend manifest")
    if not 0 < len(files) + 1 <= MAX_FILES:
        raise ValueError("Too many frontend files")
    entries = {"asset-manifest.json": manifest_bytes}
    total = len(manifest_bytes)
    for name, expected in files.items():
        if (not name or name.startswith("/") or "\\" in name or ":" in name
                or any(part in {"", ".", ".."} for part in name.split("/"))):
            raise ValueError("Unsafe frontend path")
        path = frontend / name
        if path.is_symlink() or not path.resolve().is_relative_to(frontend.resolve()):
            raise ValueError("Frontend path escapes its build root")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected or len(content) != sizes[name]:
            raise ValueError("Frontend checksum or size mismatch")
        total += len(content)
        if total > MAX_UNPACKED:
            raise ValueError("Frontend exceeds unpacked size limit")
        entries[name] = content
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            item = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, content)
    if target.stat().st_size > MAX_ZIP:
        target.unlink()
        raise ValueError("Frontend exceeds compressed size limit")
    return {"id": hashlib.sha256(manifest_bytes).hexdigest(), "size": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "unpackedSize": total,
            "fileCount": len(entries)}


def publish(frontend, output, private_path, public_path, sequence, version, installers=None):
    """Build immutable ZIP first; only then atomically expose its signed manifest."""
    private = serialization.load_pem_private_key(Path(private_path).read_bytes(), password=None)
    public = serialization.load_pem_public_key(Path(public_path).read_bytes())
    if (not isinstance(private, rsa.RSAPrivateKey) or private.key_size != 3072
            or private.public_key().public_numbers() != public.public_numbers()):
        raise ValueError("Private release key does not match the pinned RSA-3072 public key")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ui-publish-", dir=output) as temporary:
        archive = Path(temporary) / "ui.zip"
        bundle = package_frontend(frontend, archive)
        bundle.update(version=version, url=f'/api/client/ui/{bundle["id"]}.zip',
                      protocolMin=1, protocolMax=1, bridgeMin=2, minClientVersion="1.0.10",
                      capabilities=["ui_updates", "image_preview"])
        payload = validate_payload({"format": 1, "sequence": sequence, "bundle": bundle,
                                    "installers": installers or {}})
        data = canonical(payload)
        envelope = canonical({"ok": True, "available": True, "payload": base64.b64encode(data).decode("ascii"),
                              "signature": base64.b64encode(private.sign(data, padding.PKCS1v15(), hashes.SHA256())).decode("ascii")})
        if len(envelope) > MAX_METADATA:
            raise ValueError("Update metadata too large")
        destination = output / "bundles" / (bundle["id"] + ".zip")
        destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != bundle["sha256"]:
                raise ValueError("Immutable UI bundle already exists with different content")
        else:
            os.replace(archive, destination)
        atomic_write(output / "update.json", envelope)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-key", type=Path, default=ROOT / "clients/signing/ui-update-private.pem")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "client-ui-release")
    parser.add_argument("--sequence", type=int)
    parser.add_argument("--source-version", required=True, help="Exact source commit represented by this release")
    parser.add_argument("--installers", type=Path, help="Reviewed JSON map of existing published installer paths/hashes")
    args = parser.parse_args()
    # A clean directory prevents removed assets from leaking out of an earlier build.
    with tempfile.TemporaryDirectory(prefix="xtyf-ui-build-") as temporary:
        build(Path(temporary))
        sequence = reserve_sequence(args.private_key, args.sequence)
        installers = json.loads(args.installers.read_text(encoding="utf-8")) if args.installers else {}
        payload = publish(temporary, args.output_dir, args.private_key, ROOT / "clients/update-public-key.pem",
                          sequence, args.source_version, installers)
        print(f'UI release {payload["sequence"]}: {payload["bundle"]["id"]} ({payload["bundle"]["size"]} bytes)')


if __name__ == "__main__":
    main()
