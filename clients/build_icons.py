"""Generate borderless platform launcher icons from the checked-in vector source.

Only a transparent 3% safety inset is added. Do not embed the old JPEG on a
white square: it caused unwanted horizontal letterboxing on installed icons.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "clients/assets/xtyf-logo.svg"
DESKTOP = ROOT / "clients/desktop"
ANDROID = ROOT / "clients/android/res/drawable-nodpi"


def main():
    cli = DESKTOP / "node_modules/@tauri-apps/cli/tauri.js"
    if not cli.is_file():
        raise SystemExit("Install the pinned icon converter first: npm ci --prefix clients/desktop")
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required to run the pinned Tauri icon converter.")
    root = ET.parse(SOURCE).getroot()
    # The original vector is 796.87 × 838.97; expand its viewBox uniformly.
    # This produces transparent breathing room rather than the old white bands.
    root.set("width", "1024")
    root.set("height", "1024")
    root.set("viewBox", "-24 -25 844.87 888.97")
    with tempfile.TemporaryDirectory(prefix="xtyf-icons-") as directory:
        work = Path(directory)
        source = work / "app-icon.svg"
        ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)
        generated = work / "desktop"
        android = work / "android"
        for output, options in ((generated, []), (android, ["--png", "512"])):
            subprocess.run(
                [node, str(cli), "icon", str(source), "--output", str(output), *options],
                cwd=ROOT, check=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
            )
        resources = {
            generated / "icon.ico": DESKTOP / "icons/icon.ico",
            generated / "128x128.png": DESKTOP / "icons/128x128.png",
            generated / "128x128@2x.png": DESKTOP / "icons/256x256.png",
            android / "512x512.png": ANDROID / "xtyf_launcher.png",
        }
        for icon, destination in resources.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(icon, destination)
            print(destination.relative_to(ROOT))


if __name__ == "__main__":
    main()
