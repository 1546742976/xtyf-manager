#!/bin/sh
set -eu
# Run in the existing Ubuntu 22.04 WSL distro. ARM packages are extracted into
# an isolated sysroot; do not change the host's architectures or apt sources.
cache="$HOME/.cache/xtyf-manager-build"
mkdir -p "$cache"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential pkg-config libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev librsvg2-dev libayatana-appindicator3-dev gcc-aarch64-linux-gnu g++-aarch64-linux-gnu rpm binutils cpio libarchive-tools xz-utils curl ca-certificates patchelf
export CARGO_HOME="$cache/cargo" RUSTUP_HOME="$cache/rustup"
if [ ! -x "$CARGO_HOME/bin/rustup" ]; then
  curl --fail --location --retry 3 https://sh.rustup.rs -o "$cache/rustup-init.sh"
  sh "$cache/rustup-init.sh" -y --no-modify-path --profile minimal --default-toolchain 1.99.1
fi
export PATH="$CARGO_HOME/bin:$PATH"
rustup target add aarch64-unknown-linux-gnu
arm="$cache/arm64"
mkdir -p "$arm/lists/partial" "$arm/archives/partial" "$arm/sysroot"
: > "$arm/status"
cat > "$arm/sources.list" <<'SOURCES'
deb [arch=arm64] https://ports.ubuntu.com/ubuntu-ports jammy main universe
deb [arch=arm64] https://ports.ubuntu.com/ubuntu-ports jammy-updates main universe
deb [arch=arm64] https://ports.ubuntu.com/ubuntu-ports jammy-security main universe
SOURCES
apt_arm() {
  apt-get -o APT::Architecture=arm64 -o APT::Architectures=arm64 -o Dir::Etc::sourcelist="$arm/sources.list" -o Dir::Etc::sourceparts=- -o Dir::State::lists="$arm/lists" -o Dir::State::status="$arm/status" -o Dir::Cache::archives="$arm/archives" "$@"
}
apt_arm update
apt_arm install --download-only --no-install-recommends -y libwebkit2gtk-4.1-dev libgtk-3-dev libxdo-dev librsvg2-dev libayatana-appindicator3-dev
for archive in "$arm/archives/"*.deb; do dpkg-deb -x "$archive" "$arm/sysroot"; done
# Development-package absolute symlinks must stay inside this isolated sysroot
# (for example libz.so -> /lib/aarch64-linux-gnu/libz.so.1.2.11).
python3 - "$arm/sysroot" <<'PYTHON'
import os
from pathlib import Path
import sys
root = Path(sys.argv[1]).resolve()
for directory in (root / 'usr/lib/aarch64-linux-gnu', root / 'lib/aarch64-linux-gnu'):
    for link in directory.glob('*.so*'):
        if link.is_symlink():
            target = link.readlink()
            if target.is_absolute():
                local_target = root / str(target).lstrip('/')
                if local_target.is_file():
                    link.unlink()
                    link.symlink_to(os.path.relpath(local_target, link.parent))
PYTHON
printf '%s\n' 'TAURI_LINUX_SETUP_OK'
