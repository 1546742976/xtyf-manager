"""Build Tauri Ubuntu DEB and Fedora RPM for x86_64 and ARM64 in Ubuntu WSL.

--setup installs the native/cross toolchain and checksum-pinned Linux Node.js.
--check is read-only; builds use a Linux-only cache, never Windows node_modules.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NODE_VERSION = '24.22.0'
NODE_SHA256 = 'cc2f601cee82d9a5a233302fe307abc600727035fd7d2c3a3571755989e95008'
RUST_VERSION = '1.99.1'
ARM_TARGET = 'aarch64-unknown-linux-gnu'
HOST_TARGET = 'x86_64-unknown-linux-gnu'


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def capture(args, **kwargs):
    return run(args, stdout=subprocess.PIPE, encoding='utf-8', **kwargs).stdout.strip()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def arm_environment(env, cache):
    result = dict(env)
    sysroot = cache / 'arm64/sysroot'
    # Target-suffixed variables leave host proc-macros/build tools using host libraries.
    result.update({
        'CC_aarch64_unknown_linux_gnu': 'aarch64-linux-gnu-gcc',
        'CXX_aarch64_unknown_linux_gnu': 'aarch64-linux-gnu-g++',
        'CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER': 'aarch64-linux-gnu-gcc',
        'CFLAGS_aarch64_unknown_linux_gnu': f'--sysroot={sysroot}',
        'CXXFLAGS_aarch64_unknown_linux_gnu': f'--sysroot={sysroot}',
        'PKG_CONFIG_ALLOW_CROSS_aarch64_unknown_linux_gnu': '1',
        'PKG_CONFIG_SYSROOT_DIR_aarch64_unknown_linux_gnu': str(sysroot),
        'PKG_CONFIG_LIBDIR_aarch64_unknown_linux_gnu': ':'.join(str(sysroot / sub) for sub in (
            'usr/lib/aarch64-linux-gnu/pkgconfig', 'usr/lib/pkgconfig', 'usr/share/pkgconfig')),
        'CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUSTFLAGS': ' '.join((
            f'-C link-arg=--sysroot={sysroot}',
            f'-C link-arg=-Wl,-rpath-link,{sysroot}/usr/lib/aarch64-linux-gnu',
            f'-C link-arg=-Wl,-rpath-link,{sysroot}/lib/aarch64-linux-gnu',
        )),
    })
    return result


def linux_environment(setup=False):
    if platform.machine() not in ('x86_64', 'amd64'):
        raise RuntimeError('The Linux builder requires an x86_64 host; ARM64 is cross-compiled.')
    cache = Path.home() / '.cache/xtyf-manager-build'
    node_dir = cache / f'node-v{NODE_VERSION}-linux-x64'
    if setup:
        run(['sh', ROOT / 'clients/setup_linux.sh'])
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / f'{node_dir.name}.tar.xz'
        if not archive.exists() or sha256(archive) != NODE_SHA256:
            run(['curl', '--fail', '--location', '--retry', '3', '--output', archive,
                 f'https://nodejs.org/dist/v{NODE_VERSION}/{archive.name}'])
        if sha256(archive) != NODE_SHA256:
            raise RuntimeError('Node.js download failed its official SHA256 check')
        run(['tar', '-xJf', archive, '-C', cache])
    toolchain = cache / f'rustup/toolchains/{RUST_VERSION}-{HOST_TARGET}'
    env = dict(os.environ)
    env.update(PATH=f'{node_dir}/bin:{toolchain}/bin:{cache}/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
               LC_ALL='C.UTF-8', CARGO_HOME=str(cache / 'cargo'), RUSTUP_HOME=str(cache / 'rustup'),
               RUSTUP_TOOLCHAIN=RUST_VERSION, npm_config_cache=str(cache / 'npm'),
               CARGO_BUILD_JOBS=os.environ.get('XTYF_BUILD_JOBS', '4'))
    for key in ('PKG_CONFIG_SYSROOT_DIR', 'PKG_CONFIG_LIBDIR', 'PKG_CONFIG_PATH'):
        env.pop(key, None)
    missing = [tool for tool in ('node', 'npm', 'cargo', 'rustc', 'rpm', 'rpmbuild', 'bsdtar',
                                'dpkg-deb', 'readelf', 'xz', 'curl', 'pkg-config',
                                'aarch64-linux-gnu-gcc', 'aarch64-linux-gnu-g++')
               if not shutil.which(tool, path=env['PATH'])]
    if missing or not (toolchain / 'bin/rustc').is_file():
        raise RuntimeError('Missing Linux tools: ' + ', '.join(missing or ['Rust ' + RUST_VERSION]) + '. Run --setup.')
    if capture(['node', '--version'], env=env) != f'v{NODE_VERSION}':
        raise RuntimeError(f'Expected pinned Linux Node.js v{NODE_VERSION}')
    if not capture(['rustc', '--version'], env=env).startswith('rustc ' + RUST_VERSION + ' '):
        raise RuntimeError(f'Expected Rust {RUST_VERSION}')
    if not (toolchain / f'lib/rustlib/{ARM_TARGET}/lib').is_dir():
        raise RuntimeError('Missing Rust ARM64 target. Run --setup.')
    run(['pkg-config', '--exists', 'webkit2gtk-4.1', 'gtk+-3.0'], env=env)
    cross = arm_environment(env, cache)
    check_cross = dict(env, PKG_CONFIG_SYSROOT_DIR=cross['PKG_CONFIG_SYSROOT_DIR_aarch64_unknown_linux_gnu'],
                       PKG_CONFIG_LIBDIR=cross['PKG_CONFIG_LIBDIR_aarch64_unknown_linux_gnu'])
    run(['pkg-config', '--exists', 'webkit2gtk-4.1', 'gtk+-3.0'], env=check_cross)
    return cache, env


def verify_package(package, target, config, source, executable_source, env, cache, architecture=None):
    arch = architecture or ('arm64' if target == 'deb' else 'x86_64')
    if arch not in ('arm64', 'x86_64'):
        raise ValueError('Unsupported Linux architecture')
    machine = 183 if arch == 'arm64' else 62
    package_arch = ('arm64' if arch == 'arm64' else 'amd64') if target == 'deb' else ('aarch64' if arch == 'arm64' else 'x86_64')
    if target == 'deb':
        metadata = [capture(['dpkg-deb', '--field', package, field], env=env)
                    for field in ('Package', 'Version', 'Architecture')]
        dependencies = capture(['dpkg-deb', '--field', package, 'Depends'], env=env).split(', ')
    else:
        metadata = capture(['rpm', '-qp', '--queryformat', '%{NAME}\n%{VERSION}\n%{ARCH}', package], env=env).splitlines()
        dependencies = capture(['rpm', '-qp', '--requires', package], env=env).splitlines()
        capture(['rpm', '--checksig', package], env=env)
    match = re.fullmatch(r"(\d+\.\d+\.\d+)-beta\.(\d+)", config["version"])
    metadata_version = config["version"]
    if match:
        metadata_version = match[1] + "~beta." + match[2] if target == "deb" else match[1]
        if target == "rpm":
            release = capture(['rpm', '-qp', '--queryformat', '%{RELEASE}', package], env=env)
            if release != "0.beta." + match[2]:
                raise RuntimeError(f"Incorrect pre-release RPM ordering: {release}")
    elif target == "rpm":
        release = capture(['rpm', '-qp', '--queryformat', '%{RELEASE}', package], env=env)
        if release != "1":
            raise RuntimeError(f"Incorrect stable RPM release: {release}")
    if metadata != ['xtyf-manager', metadata_version, package_arch]:
        raise RuntimeError(f'Incorrect {target} package metadata: {metadata}')
    settings = json.loads((source / 'tauri.conf.json').read_text(encoding='utf-8'))
    expected_dependencies = settings['bundle']['linux'][target]['depends']
    if not all(dep in dependencies for dep in expected_dependencies):
        raise RuntimeError(f'Missing {target} dependencies: {dependencies}')
    with tempfile.TemporaryDirectory(prefix='verify-', dir=cache) as temporary:
        extracted = Path(temporary)
        if target == 'deb':
            run(['dpkg-deb', '--extract', package, extracted], env=env)
        else:
            run(['bsdtar', '--extract', '--file', package, '--directory', extracted], env=env)
        desktop = extracted / 'usr/share/applications/xtyf-manager.desktop'
        desktop_text = desktop.read_text(encoding='utf-8')
        if any(required not in desktop_text for required in (
            'Exec=xtyf-manager', 'Name=巡天御风管理系统', 'Type=Application', 'Icon=xtyf-manager',
        )) or '--no-sandbox' in desktop_text:
            raise RuntimeError('Invalid Linux desktop entry')
        if not list((extracted / 'usr/share/icons').rglob('xtyf-manager.png')):
            raise RuntimeError('Missing desktop icon')
        executable = extracted / 'usr/bin/xtyf-manager'
        if not executable.is_file() or not executable.stat().st_mode & 0o111:
            raise RuntimeError('Missing executable or execute permissions')
        compiled = executable_source.read_bytes()
        binary_data = executable.read_bytes()
        unknown_marker = b'__TAURI_BUNDLE_TYPE_VAR_UNK'
        bundle_marker = b'__TAURI_BUNDLE_TYPE_VAR_' + target.upper().encode('ascii')
        # Tauri patches its reserved bundle-type slot in the packaged copy and
        # restores the original executable after bundling. No other byte may vary.
        if compiled.count(unknown_marker) != 1 or compiled.replace(unknown_marker, bundle_marker) != binary_data:
            raise RuntimeError('Package executable differs beyond the Tauri bundle-type marker')
        glibc, needed, elf_count = set(), set(), 0
        for path in extracted.rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            with path.open('rb') as stream:
                header = stream.read(20)
            if header[:4] != b'\x7fELF':
                continue
            elf_count += 1
            if header[4] != 2 or header[5] != 1 or int.from_bytes(header[18:20], 'little') != machine:
                raise RuntimeError(f'Unexpected ELF architecture: {path.name}')
            symbols = capture(['readelf', '--version-info', path], env=env)
            glibc.update(tuple(map(int, value.split('.'))) for value in re.findall(r'\bGLIBC_(\d+(?:\.\d+)+)', symbols))
            needed.update(re.findall(r'Shared library: \[([^]]+)\]', capture(['readelf', '--dynamic', path], env=env)))
        if not elf_count or not glibc:
            raise RuntimeError('No inspectable Tauri ELF payload found')
        required_glibc = max(glibc)
        if target == 'deb' and required_glibc > (2, 35):
            raise RuntimeError(f'Ubuntu 22.04 GLIBC 2.35 is insufficient: {required_glibc}')
        if not {'libwebkit2gtk-4.1.so.0', 'libgtk-3.so.0'}.issubset(needed):
            raise RuntimeError('Expected system WebKitGTK/GTK dependencies not found in executable')
        manifest = extracted / 'usr/lib/xtyf-manager/asset-manifest.json'
        frontend = source.parent / 'frontend/dist'
        if not manifest.is_file() or manifest.read_bytes() != (frontend / 'asset-manifest.json').read_bytes():
            raise RuntimeError('Packaged frontend manifest does not match the shared local frontend')
        if manifest.stat().st_mode & 0o022:
            raise RuntimeError('Packaged frontend manifest must not be group/world writable')
        if b'X-XTYF-Client-Protocol' not in binary_data:
            raise RuntimeError('Native client protocol implementation missing from executable')
        if manifest.read_bytes() not in binary_data:
            raise RuntimeError('Frontend manifest is not embedded in the Tauri executable')
        if arch == 'x86_64':
            build_info = extracted / 'build-info.json'
            run([executable, '--build-info', build_info], env=env)
            info = json.loads(build_info.read_text(encoding='utf-8'))
            if (info.get('version') != config['version'] or info.get('architecture') != ('aarch64' if arch == 'arm64' else 'x86_64')
                    or info.get('framework') != 'tauri' or info.get('frontend') != json.loads(manifest.read_text(encoding='utf-8'))):
                raise RuntimeError('Native build information disagrees with package/frontend metadata')
        result = {
            'framework': 'Tauri 2', 'webview': 'WebKitGTK 4.1',
            'distribution': 'Ubuntu 22.04' if target == 'deb' else 'Fedora 44/45',
            'architecture': arch, 'format': target, 'version': config['version'],
            'packageMetadataVerified': True, 'elfArchitectureVerified': True,
            'glibcRequired': '.'.join(map(str, required_glibc)), 'elfFilesChecked': elf_count,
            'dependencies': dependencies, 'neededSharedLibraries': sorted(needed),
            'desktopEntryVerified': True, 'clientPayloadVerified': True,
            'localFrontendManifestSha256': sha256(manifest),
            'embeddedFrontendManifestVerified': True,
            'nativeBuildInfoExecuted': arch == 'x86_64',
            'runtimeUiVerified': False, 'targetRuntimeStatus': 'not-tested',
        }
        if target == 'deb':
            result['glibcLimit'] = '2.35'
        else:
            result['fedora45Status'] = 'not-runtime-tested'
        return result


def build(root, output, cache, env):
    config = json.loads((root / 'clients/config.json').read_text(encoding='utf-8'))
    desktop = root / 'clients/desktop'
    frontend = root / 'clients/frontend/dist'
    for required in (desktop / 'Cargo.lock', desktop / 'package-lock.json', frontend / 'asset-manifest.json',
                     root / 'clients/update-public-key.pem'):
        if not required.is_file():
            raise RuntimeError(f'Missing build input: {required}; build the shared frontend first.')
    manifest_data = json.loads((frontend / 'asset-manifest.json').read_text(encoding='utf-8'))
    tauri_config = json.loads((desktop / 'tauri.conf.json').read_text(encoding='utf-8'))
    cargo_version = re.search(r'^version\s*=\s*"([^"]+)"', (desktop / 'Cargo.toml').read_text(encoding='utf-8'), re.M)
    if (manifest_data.get('version') != config['version'] or tauri_config.get('version') != config['version']
            or not cargo_version or cargo_version[1] != config['version']):
        raise RuntimeError('Frontend, Tauri and Cargo versions must match clients/config.json')
    project_id = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    project = cache / f'tauri-project-{project_id}'
    project.mkdir(parents=True, exist_ok=True)
    # Keep expensive target artifacts outside clean source staging.
    inputs = project / 'clients'
    if inputs.exists():
        if inputs.resolve().parent != project.resolve() or inputs.is_symlink():
            raise RuntimeError(f'Unexpected staging directory: {inputs}')
        shutil.rmtree(inputs)
    source = inputs / 'desktop'
    shutil.copytree(desktop, source, ignore=shutil.ignore_patterns('node_modules', 'target', 'gen', '.git'))
    shutil.copytree(frontend, inputs / 'frontend/dist')
    shutil.copy2(root / 'clients/config.json', inputs / 'config.json')
    shutil.copy2(root / 'clients/update-public-key.pem', inputs / 'update-public-key.pem')
    # NTFS source permissions appear as 0777 in WSL. Tauri's RPM resources
    # preserve modes, so normalize bundled non-executable assets before build.
    for assets in (source / 'icons', inputs / 'frontend/dist'):
        for entry in assets.rglob('*'):
            entry.chmod(0o755 if entry.is_dir() else 0o644)
    env = dict(env, CARGO_TARGET_DIR=str(project / 'target'))
    run(['npm', 'ci', '--no-audit', '--no-fund'], cwd=source, env=env)
    cli = source / 'node_modules/.bin/tauri'
    report = {}
    artifacts = []
    for target, triple, key, suffix in (
        ('rpm', HOST_TARGET, 'fedora', 'x86_64.rpm'),
        ('deb', ARM_TARGET, 'ubuntu', 'arm64.deb'),
        ('deb', HOST_TARGET, 'ubuntu-amd64', 'amd64.deb'),
        ('rpm', ARM_TARGET, 'fedora-aarch64', 'aarch64.rpm'),
    ):
        target_env = arm_environment(env, cache) if triple == ARM_TARGET else env
        run([cli, 'build', '--ci', '--target', triple, '--bundles', target, '--', '--locked'], cwd=source, env=target_env)
        release = project / 'target' / triple / 'release'
        packages = list((release / 'bundle' / target).glob(f'*{config["version"]}*.{target}'))
        if len(packages) != 1:
            raise RuntimeError(f'Expected one current {target} package, found {packages}')
        report[key] = verify_package(packages[0], target, config, source, release / 'xtyf-manager', env, cache, architecture='arm64' if triple == ARM_TARGET else 'x86_64')
        artifacts.append((packages[0], f'XTYF-Manager-{config["version"]}-{suffix}'))
    report['builder'] = {
        'environment': platform.freedesktop_os_release()['PRETTY_NAME'] + ' ' + platform.machine(),
        'nodeVersion': capture(['node', '--version'], env=env),
        'rustVersion': capture(['rustc', '--version'], env=env),
        'tauriCliVersion': capture([cli, '--version'], cwd=source, env=env),
    }
    output.mkdir(parents=True, exist_ok=True)
    for package, name in artifacts:
        shutil.copy2(package, output / name)
    (output / 'linux-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('LINUX_BUILD_OK ' + config['version'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist/clients/linux')
    parser.add_argument('--wsl-distro', default='Ubuntu-22.04')
    parser.add_argument('--wsl-user', default='root')
    parser.add_argument('--check', action='store_true', help='Read-only prerequisite check')
    parser.add_argument('--setup', action='store_true', help='Install Linux native/cross prerequisites and pinned Node.js')
    args = parser.parse_args()
    if args.check and args.setup:
        parser.error('--check and --setup are mutually exclusive')
    if os.name == 'nt':
        prefix = ['wsl.exe', '-d', args.wsl_distro, '-u', args.wsl_user, '--exec']
        script = capture([*prefix, 'wslpath', '-a', str(Path(__file__).resolve())])
        output = capture([*prefix, 'wslpath', '-a', str(args.output_dir.resolve())])
        command = [*prefix, 'python3', script, '--output-dir', output]
        if args.check:
            command.append('--check')
        if args.setup:
            command.append('--setup')
        run(command)
    else:
        cache, env = linux_environment(args.setup)
        if args.check or args.setup:
            print(f'LINUX_TOOLS_OK Tauri/Rust {RUST_VERSION}, Node.js {NODE_VERSION}', flush=True)
        else:
            build(ROOT, args.output_dir.resolve(), cache, env)


if __name__ == '__main__':
    main()
