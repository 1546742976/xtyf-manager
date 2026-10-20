"""Build and verify native x86_64 / aarch64 Tauri DEB and RPM on CI runners.

Unlike build_linux.py (cross-built local WSL release), this entry point runs on
an actual Ubuntu 22.04 runner of the same architecture as the intended payload.
It never generates a formal release or modifies version metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import json
import platform
from pathlib import Path
import shutil
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients.build_linux import verify_package

ROOT = Path(__file__).resolve().parents[1]
ARCHES = {"x86_64": "x86_64", "aarch64": "arm64"}


def architecture(machine=None):
    machine = machine or platform.machine()
    if machine not in ARCHES:
        raise RuntimeError("CI packaging requires native x86_64 or aarch64 runner")
    return ARCHES[machine]


def build(output):
    arch = architecture()
    config = json.loads((ROOT / "clients/config.json").read_text(encoding="utf-8"))
    desktop = ROOT / "clients/desktop"
    manifest = ROOT / "clients/frontend/dist/asset-manifest.json"
    if not manifest.is_file() or json.loads(manifest.read_text(encoding="utf-8")).get("version") != config["version"]:
        raise RuntimeError("Missing/mismatched pinned frontend; build it before packaging")
    if not (desktop / "node_modules/.bin/tauri").is_file():
        raise RuntimeError("Install pinned Tauri CLI with npm ci --prefix clients/desktop")
    executable = desktop / "target/release/xtyf-manager"
    cli = desktop / "node_modules/.bin/tauri"
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    sums = []
    for target, name, suffix in (
        ("deb", "ubuntu", "amd64.deb" if arch == "x86_64" else "arm64.deb"),
        ("rpm", "fedora", "x86_64.rpm" if arch == "x86_64" else "aarch64.rpm"),
    ):
        subprocess.run([str(cli), "build", "--ci", "--bundles", target, "--", "--locked"], cwd=desktop, check=True)
        candidates = list((desktop / "target/release/bundle" / target).glob(f"*{config['version']}*.{target}"))
        if len(candidates) != 1:
            raise RuntimeError(f"Expected exactly one {target} package: {candidates}")
        source = candidates[0]
        result = verify_package(source, target, config, desktop, executable, os.environ.copy(), output, arch)
        destination = output / f"XTYF-Manager-{config['version']}-{suffix}"
        shutil.copy2(source, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        sums.append(f"{digest}  {destination.name}\n")
        results[name] = result
    (output / "SHA256SUMS.txt").write_text("".join(sums), encoding="ascii")
    (output / "linux-verification.json").write_text(
        json.dumps({"architecture": arch, "nativeRunner": True, **results},
                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/clients/linux")
    build(parser.parse_args().output_dir.resolve())
