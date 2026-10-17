"""Regenerate native app icons from the original team logo without packaging."""
from __future__ import annotations

import base64
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "static/brand/xtyf-red-blue.jpg"
DESKTOP = ROOT / "clients/desktop"
ANDROID = ROOT / "clients/android/res/drawable-nodpi"


def main():
    cli = DESKTOP / "node_modules/@tauri-apps/cli/tauri.js"
    if not cli.is_file():
        raise SystemExit("Install the pinned desktop CLI with npm ci --prefix clients/desktop first.")
    node = shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required to run the pinned Tauri icon converter.")

    encoded = base64.b64encode(SOURCE.read_bytes()).decode("ascii")
    # Keep the supplied artwork, proportions and white background. The margin
    # prevents its edges touching the desktop tile or the Android launcher mask.
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="1024" height="1024" viewBox="0 0 1024 1024">'
        '<rect width="1024" height="1024" fill="#fff"/>'
        '<image x="64" y="64" width="896" height="896" preserveAspectRatio="xMidYMid meet" '
        f'xlink:href="data:image/jpeg;base64,{encoded}"/>'
        '</svg>'
    )
    with tempfile.TemporaryDirectory(prefix="xtyf-icons-") as directory:
        work = Path(directory)
        source = work / "app-icon.svg"
        source.write_text(svg, encoding="utf-8")
        generated = work / "desktop"
        android = work / "android"
        for output, options in ((generated, []), (android, ["--png", "512"])):
            subprocess.run(
                [node, str(cli), "icon", str(source), "--output", str(output), *options],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        resources = {
            generated / "icon.ico": DESKTOP / "icons/icon.ico",
            generated / "128x128.png": DESKTOP / "icons/128x128.png",
            generated / "128x128@2x.png": DESKTOP / "icons/256x256.png",
            android / "512x512.png": ANDROID / "xtyf_launcher.png",
        }
        for source, destination in resources.items():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            print(destination.relative_to(ROOT))


if __name__ == "__main__":
    main()
