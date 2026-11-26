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
import re
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients.build_linux import verify_package
from clients.versioning_ci import BETA

ROOT = Path(__file__).resolve().parents[1]
ARCHES = {"x86_64": "x86_64", "aarch64": "arm64"}


def architecture(machine=None):
    machine = machine or platform.machine()
    if machine not in ARCHES:
        raise RuntimeError("CI packaging requires native x86_64 or aarch64 runner")
    return ARCHES[machine]


def normalize_debian_version(source, destination, version):
    """Debian treats a raw hyphen beta as a revision newer than stable.
    Repack with ~beta so apt correctly upgrades a test package to stable.
    """
    match=BETA.fullmatch(version)
    if not match:
        return source
    with tempfile.TemporaryDirectory(prefix="deb-repack-",dir=destination.parent) as temp:
        root=Path(temp)/"root"
        subprocess.run(["dpkg-deb","--raw-extract",str(source),str(root)],check=True)
        control=root/"DEBIAN/control"
        data=control.read_text(encoding="utf-8")
        encoded=version.replace("-beta.","~beta.")
        rewritten,n=re.subn(r"(?m)^Version: .+$",f"Version: {encoded}",data)
        if n!=1:raise RuntimeError("Missing Debian package Version field")
        control.write_text(rewritten,encoding="utf-8")
        subprocess.run(["dpkg-deb","--build","--root-owner-group",str(root),str(destination)],check=True)
    return destination


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
        beta=BETA.fullmatch(config["version"])
        original_settings=(desktop / "tauri.conf.json").read_text(encoding="utf-8")
        # RPM has separate Version and Release: beta packages need release 0.beta.N.
        # Compile the native executable with the actual beta version; only the
        # packaging metadata is normalized for the RPM bundler.
        if beta and target=="rpm":
            settings=json.loads(original_settings)
            settings["version"]=config["version"].split("-")[0]
            settings["bundle"]["linux"]["rpm"]["release"]="0.beta."+beta[4]
            (desktop/"tauri.conf.json").write_text(json.dumps(settings,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        try:
            subprocess.run([str(cli),"build","--ci","--bundles",target,"--","--locked"],cwd=desktop,check=True)
        finally:
            (desktop/"tauri.conf.json").write_text(original_settings,encoding="utf-8")
        candidates=list((desktop/"target/release/bundle"/target).glob("*."+target))
        if len(candidates)!=1:raise RuntimeError(f"Expected exactly one {target} package: {candidates}")
        source=candidates[0]
        if beta and target=="deb":
            source=normalize_debian_version(source,output/"repacked-beta.deb",config["version"])
        result=verify_package(source,target,config,desktop,executable,os.environ.copy(),output,arch)
        destination=output/f"XTYF-Manager-{config['version']}-{suffix}"
        shutil.copy2(source,destination)
        if source.name=="repacked-beta.deb":source.unlink()
        digest=hashlib.sha256(destination.read_bytes()).hexdigest()
        sums.append(f"{digest}  {destination.name}\n")
        results[name]=result
    (output / "SHA256SUMS.txt").write_text("".join(sums), encoding="ascii")
    (output / "linux-verification.json").write_text(
        json.dumps({"architecture": arch, "nativeRunner": True, **results},
                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/clients/linux")
    build(parser.parse_args().output_dir.resolve())
