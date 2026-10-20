"""Check per-run CI installers before publishing a GitHub Release.

GitHub Actions publishes binary artifacts as distinct ZIPs. This script consumes
already-extracted artifacts, validates filenames, versions, and per-job digests,
then produces one flat, release-ready directory with a consolidated SHA256SUMS.
It must run after ALL platform build jobs succeed.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil


def expected_artifacts(version: str) -> dict[str, list[str]]:
    return {
        "xtyf-client-windows-x64": [f"XTYF-Manager-Setup-{version}-x64.exe"],
        "xtyf-client-windows-arm64": [f"XTYF-Manager-Setup-{version}-arm64.exe"],
        "xtyf-client-android-apk": [f"XTYF-Manager-{version}.apk"],
        "xtyf-client-linux-x64": [
            f"XTYF-Manager-{version}-amd64.deb",
            f"XTYF-Manager-{version}-x86_64.rpm",
        ],
        "xtyf-client-linux-arm64": [
            f"XTYF-Manager-{version}-arm64.deb",
            f"XTYF-Manager-{version}-aarch64.rpm",
        ],
    }


def validate_tag(tag: str, channel: str, version: str) -> None:
    if channel == "stable":
        if tag != f"v{version}":
            raise ValueError(f"Stable tag {tag!r} must equal v{version}")
    elif channel == "prerelease":
        if not re.fullmatch(r"[0-9]{8}-beta[1-9][0-9]*", tag):
            raise ValueError(f"Invalid PR beta tag: {tag!r}")
        datetime.strptime(tag[:8], "%Y%m%d")
    else:
        raise ValueError(f"Unknown channel: {channel}")


def read_checksums(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing trustworthy checksum manifest: {path}")
    result = {}
    for line in path.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match:
            raise ValueError(f"Invalid checksum entry in {path}: {line!r}")
        digest, filename = match.groups()
        if filename in result:
            raise ValueError(f"Duplicate checksum entry: {filename}")
        result[filename] = digest
    return result


def collect(artifacts_dir: Path, output_dir: Path, *, version: str, tag: str, channel: str) -> list[dict]:
    validate_tag(tag, channel, version)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"Output directory is not empty: {output_dir}")
    expected = expected_artifacts(version)
    if not artifacts_dir.is_dir() or artifacts_dir.is_symlink():
        raise ValueError(f"Artifacts directory missing: {artifacts_dir}")
    found = {p.name for p in artifacts_dir.iterdir()}
    if found != set(expected):
        raise ValueError(f"Missing or unexpected CI artifact directories: {sorted(found ^ set(expected))}")
    records = []
    for artifact, filenames in expected.items():
        directory = artifacts_dir / artifact
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"Invalid artifact directory: {directory}")
        sums = read_checksums(directory / "SHA256SUMS.txt")
        if set(sums) != set(filenames):
            raise ValueError(f"Unexpected files in {artifact} checksums: {sorted(set(sums) ^ set(filenames))}")
        for filename in filenames:
            package = directory / filename
            if not package.is_file() or package.is_symlink() or package.stat().st_size < 1024:
                raise ValueError(f"Missing/invalid installer: {package}")
            with package.open("rb") as binary:
                digest = hashlib.file_digest(binary, "sha256").hexdigest()
            if sums[filename] != digest:
                raise ValueError(f"Checksum mismatch for {package}")
            records.append({"name": filename, "bytes": package.stat().st_size, "sha256": digest, "artifact": artifact})
    if len({r["name"] for r in records}) != len(records):
        raise ValueError("Duplicate installer filename")
    output_dir.mkdir(parents=True, exist_ok=True)
    for record in records:
        source = artifacts_dir / record["artifact"] / record["name"]
        shutil.copy2(source, output_dir / record["name"])
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(f'{r["sha256"]}  {r["name"]}\n' for r in sorted(records, key=lambda row: row["name"])),
        encoding="ascii",
    )
    (output_dir / "build-report.json").write_text(
        json.dumps({"tag": tag, "version": version, "channel": channel, "installers": records},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--channel", choices=["stable", "prerelease"], required=True)
    parser.add_argument("--config", type=Path, default=Path("clients/config.json"))
    args = parser.parse_args()
    version = json.loads(args.config.read_text(encoding="utf-8"))["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        parser.error(f"Invalid application version: {version}")
    records = collect(args.artifacts_dir, args.output_dir, version=version, tag=args.tag, channel=args.channel)
    print(f"Validated {len(records)} installers for {args.tag} (app version {version})")


if __name__ == "__main__":
    main()
