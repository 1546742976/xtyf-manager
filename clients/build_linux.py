"""Build and inspect Ubuntu ARM64 DEB and Fedora x86_64 RPM using Ubuntu WSL.

--setup installs a checksum-pinned, user-local Linux Node.js; system prerequisites:
sudo apt-get install rpm binutils cpio xz-utils curl ca-certificates
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
SOURCE_FILES = ('main.cjs', 'preload.cjs', 'url.cjs', 'settings.html', 'settings.js',
                'client.test.cjs', 'package.json', 'package-lock.json', 'icon-256x256.png')


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def capture(args, **kwargs):
    return run(args, stdout=subprocess.PIPE, encoding='utf-8', **kwargs).stdout.strip()


def linux_environment(setup=False):
    if platform.machine() not in ('x86_64', 'amd64'):
        raise RuntimeError('The Linux builder requires an x86_64 host (the DEB payload is ARM64).')
    cache = Path.home() / '.cache/xtyf-manager-build'
    node_dir = cache / f'node-v{NODE_VERSION}-linux-x64'
    if setup:
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / f'{node_dir.name}.tar.xz'
        if not archive.exists() or hashlib.sha256(archive.read_bytes()).hexdigest() != NODE_SHA256:
            run(['curl', '--fail', '--location', '--retry', '3', '--output', archive,
                 f'https://nodejs.org/dist/v{NODE_VERSION}/{archive.name}'])
        if hashlib.sha256(archive.read_bytes()).hexdigest() != NODE_SHA256:
            raise RuntimeError('Node.js download failed its official SHA256 check')
        run(['tar', '-xJf', archive, '-C', cache])
    env = dict(os.environ)
    # Never pick up npm or native dependencies from the Windows PATH.
    env.update(PATH=f'{node_dir}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
               LC_ALL='C.UTF-8', ELECTRON_CACHE=str(cache / 'electron'),
               ELECTRON_BUILDER_CACHE=str(cache / 'electron-builder'),
               npm_config_cache=str(cache / 'npm'), ELECTRON_SKIP_BINARY_DOWNLOAD='1',
               CSC_IDENTITY_AUTO_DISCOVERY='false')
    missing = [tool for tool in ('node', 'npm', 'rpm', 'rpmbuild', 'rpm2cpio', 'dpkg-deb',
                                'readelf', 'cpio', 'xz', 'curl') if not shutil.which(tool, path=env['PATH'])]
    if missing:
        raise RuntimeError('Missing Linux tools: ' + ', '.join(missing) +
                           '. Run the apt prerequisite command in this script, then --setup.')
    version = capture(['node', '--version'], env=env)
    if version != f'v{NODE_VERSION}':
        raise RuntimeError(f'Expected pinned Linux Node.js v{NODE_VERSION}, got {version}')
    return cache, env


def verify_package(package, target, config, source, env, cache):
    arch, machine = ('arm64', 183) if target == 'deb' else ('x86_64', 62)
    if target == 'deb':
        metadata = [capture(['dpkg-deb', '--field', package, field], env=env)
                    for field in ('Package', 'Version', 'Architecture')]
        dependencies = capture(['dpkg-deb', '--field', package, 'Depends'], env=env).split(', ')
    else:
        metadata = capture(['rpm', '-qp', '--queryformat', '%{NAME}\n%{VERSION}\n%{ARCH}', package], env=env).splitlines()
        dependencies = capture(['rpm', '-qp', '--requires', package], env=env).splitlines()
        capture(['rpm', '--checksig', package], env=env)
    if metadata != ['xtyf-manager', config['version'], arch]:
        raise RuntimeError(f'Incorrect {target} package metadata: {metadata}')
    settings = json.loads((source / 'package.json').read_text())
    expected_dependencies = settings['build'][target]['depends']
    if not all(dep in dependencies for dep in expected_dependencies):
        raise RuntimeError(f'Missing {target} dependencies: {dependencies}')
    with tempfile.TemporaryDirectory(prefix='verify-', dir=cache) as temporary:
        extracted = Path(temporary)
        if target == 'deb':
            run(['dpkg-deb', '--extract', package, extracted], env=env)
        else:
            with tempfile.TemporaryFile() as stream:
                run(['rpm2cpio', package], stdout=stream, env=env)
                stream.seek(0)
                run(['cpio', '--extract', '--make-directories', '--no-absolute-filenames', '--quiet'],
                    stdin=stream, cwd=extracted, env=env)
        desktop = extracted / 'usr/share/applications/xtyf-manager.desktop'
        text = desktop.read_text(encoding='utf-8')
        if 'xtyf-manager' not in text or '--no-sandbox' in text or 'Type=Application' not in text:
            raise RuntimeError('Invalid Linux desktop entry')
        icons = list((extracted / 'usr/share/icons').rglob('xtyf-manager.png'))
        if not icons:
            raise RuntimeError('Missing desktop icon')
        app = extracted / 'opt' / settings['build']['productName']
        executable = app / 'xtyf-manager'
        if not executable.is_file() or not executable.stat().st_mode & 0o111:
            raise RuntimeError('Missing executable or execute permissions')
        glibc = set()
        needed = set()
        elf_count = 0
        for path in app.rglob('*'):
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
            dynamic = capture(['readelf', '--dynamic', path], env=env)
            needed.update(re.findall(r'Shared library: \[([^]]+)\]', dynamic))
        if not elf_count or not glibc:
            raise RuntimeError('No inspectable Electron ELF payload found')
        required_glibc = max(glibc)
        if target == 'deb' and required_glibc > (2, 35):
            raise RuntimeError(f'Ubuntu 22.04 GLIBC 2.35 is insufficient: {required_glibc}')
        # Inspect the installed archive, not just the source tree or unpacked build.
        inspection = capture(['node', '-e',
            "const a=require('@electron/asar'), p=process.argv[1]; "
            "console.log(JSON.stringify({files:a.listPackage(p),config:JSON.parse(a.extractFile(p,'config.json')),"
            "package:JSON.parse(a.extractFile(p,'package.json')),main:a.extractFile(p,'main.cjs').toString()}));",
            app / 'resources/app.asar'], cwd=source, env=env)
        payload = json.loads(inspection)
        expected_files = {'main.cjs', 'preload.cjs', 'url.cjs', 'settings.html', 'settings.js', 'config.json', 'package.json'}
        if set(name.lstrip('/') for name in payload['files']) != expected_files:
            raise RuntimeError('Unexpected files in client archive')
        if payload['config'] != config or payload['package']['version'] != config['version']:
            raise RuntimeError('Packaged client configuration/version does not match release')
        if 'Menu.setApplicationMenu(null)' not in payload['main'] or 'mainWindow.removeMenu()' not in payload['main']:
            raise RuntimeError('Client menu-removal configuration is missing')
        result = {
            'distribution': 'Ubuntu 22.04' if target == 'deb' else 'Fedora 44/45',
            'architecture': arch, 'format': target, 'version': config['version'],
            'packageMetadataVerified': True, 'elfArchitectureVerified': True,
            'glibcRequired': '.'.join(map(str, required_glibc)), 'elfFilesChecked': elf_count,
            'dependencies': dependencies, 'neededSharedLibraries': sorted(needed),
            'desktopEntryVerified': True, 'clientPayloadVerified': True,
            'runtimeUiVerified': False, 'targetRuntimeStatus': 'not-tested',
        }
        if target == 'deb':
            result['glibcLimit'] = '2.35'
        else:
            result['fedora45Status'] = 'not-runtime-tested'
        return result


def build(root, output, cache, env):
    config = json.loads((root / 'clients/config.json').read_text(encoding='utf-8'))
    desktop_source = root / 'clients/windows'
    # Linux filesystem staging avoids NTFS executable-bit and Windows node_modules issues.
    project_id = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    source = cache / f'project-{project_id}'
    source.mkdir(parents=True, exist_ok=True)
    for name in SOURCE_FILES:
        shutil.copy2(desktop_source / name, source / name)
    shutil.copy2(root / 'clients/config.json', source / 'config.json')
    run(['npm', 'ci', '--no-audit', '--no-fund'], cwd=source, env=env)
    run(['npm', 'test'], cwd=source, env=env)
    with tempfile.TemporaryDirectory(prefix='packages-', dir=cache) as temporary:
        staging = Path(temporary)
        for target, arch in (('deb', 'arm64'), ('rpm', 'x64')):
            run([source / 'node_modules/.bin/electron-builder', '--linux', target, f'--{arch}',
                 '--publish', 'never', f'-c.directories.output={staging}',
                 f'-c.extraMetadata.version={config["version"]}'], cwd=source, env=env)
        names = [f'XTYF-Manager-{config["version"]}-arm64.deb',
                 f'XTYF-Manager-{config["version"]}-x86_64.rpm']
        report = {
            'ubuntu': verify_package(staging / names[0], 'deb', config, source, env, cache),
            'fedora': verify_package(staging / names[1], 'rpm', config, source, env, cache),
            'builder': {'environment': platform.freedesktop_os_release()['PRETTY_NAME'] + ' ' + platform.machine(),
                        'nodeVersion': capture(['node', '--version'], env=env)},
        }
        output.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(staging / name, output / name)
        (output / 'linux-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('LINUX_BUILD_OK ' + config['version'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist/clients/linux')
    parser.add_argument('--wsl-distro', default='Ubuntu-22.04')
    parser.add_argument('--check', action='store_true', help='Check build tools without building or changing versions')
    parser.add_argument('--setup', action='store_true', help='Install checksum-pinned Node.js into the Linux user cache')
    args = parser.parse_args()
    if os.name == 'nt':
        prefix = ['wsl.exe', '-d', args.wsl_distro, '--exec']
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
            print(f'LINUX_TOOLS_OK Node.js {NODE_VERSION}', flush=True)
        else:
            build(ROOT, args.output_dir.resolve(), cache, env)


if __name__ == '__main__':
    main()
