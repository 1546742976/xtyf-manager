"""Build a signed APK with the official JDK and Android SDK, without Gradle."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / 'clients'
SOURCE = CLIENT / 'android'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jdk', type=Path, default=next((ROOT / '.build-tools/jdk21').glob('jdk-*'), None))
    parser.add_argument('--sdk', type=Path, default=ROOT / '.build-tools/android')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist/clients/android')
    parser.add_argument('--skip-frontend-build', action='store_true', help='Use the shared frontend already built by release.py')
    args = parser.parse_args()
    if not args.jdk:
        parser.error('Provide --jdk pointing to a JDK 17 or newer')
    tool = next(args.sdk.rglob('aapt2.exe'), None)
    platform = next(args.sdk.rglob('android.jar'), None)
    if not tool or not platform:
        parser.error('Provide --sdk containing Android build-tools 36 and platform 36')
    tools = tool.parent
    java = args.jdk / 'bin/java.exe'
    env = dict(os.environ, JAVA_HOME=str(args.jdk))
    def run(*command):
        # Keep SDK input/output arguments relative for portability on Windows.
        arguments = [str(command[0])] + [str(v.relative_to(ROOT) if isinstance(v, Path) and v.is_relative_to(ROOT) else v) for v in command[1:]]
        subprocess.run(arguments, check=True, env=env, cwd=ROOT)
    config = json.loads((CLIENT / 'config.json').read_text(encoding='utf-8'))
    if not args.skip_frontend_build:
        subprocess.run([sys.executable, str(CLIENT / 'build_frontend.py')], check=True, cwd=ROOT)
    frontend = CLIENT / 'frontend/dist'
    if not (frontend / 'index.html').is_file() or not (frontend / 'asset-manifest.json').is_file():
        raise RuntimeError('Shared local frontend is missing; run python clients/build_frontend.py first')
    asset_manifest = json.loads((frontend / 'asset-manifest.json').read_text(encoding='utf-8'))
    if asset_manifest.get('version') != config['version']:
        raise RuntimeError('Shared frontend version does not match the Android build version')
    for name, digest in asset_manifest['files'].items():
        asset = (frontend / name).resolve()
        if not asset.is_relative_to(frontend.resolve()) or not asset.is_file() or hashlib.sha256(asset.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Shared frontend asset is missing or changed: {name}')
    out = args.output_dir.resolve()
    work = (SOURCE / 'build').resolve()
    # Compile from an empty directory so removed Java/DEX classes cannot survive.
    if work.parent != SOURCE.resolve():
        raise RuntimeError('Android build directory must stay inside clients/android')
    if work.exists():
        shutil.rmtree(work)
    for directory in (out, work / 'gen', work / 'classes', work / 'dex'):
        directory.mkdir(parents=True, exist_ok=True)
    android_ns = 'http://schemas.android.com/apk/res/android'
    ET.register_namespace('android', android_ns)
    manifest = ET.parse(SOURCE / 'AndroidManifest.xml')
    manifest.getroot().set(f'{{{android_ns}}}versionName', config['version'])
    manifest.getroot().set(f'{{{android_ns}}}versionCode', str(config['versionCode']))
    manifest.write(work / 'AndroidManifest.xml', encoding='utf-8', xml_declaration=True)
    (work / 'gen/BuildConfig.java').write_text('package online.skywalker.manager;\npublic final class BuildConfig {\n'
        + 'public static final String SERVER_URL = ' + json.dumps(config['serverUrl']) + ';\n'
        + 'public static final String VERSION = ' + json.dumps(config['version']) + ';\n}\n', encoding='utf-8')
    run(tool, 'compile', '--dir', SOURCE / 'res', '-o', work / 'resources.zip')
    run(tool, 'link', '-o', work / 'resources.apk', '-I', platform, '--manifest', work / 'AndroidManifest.xml', '--java', work / 'gen', work / 'resources.zip')
    sources = list((SOURCE / 'src').rglob('*.java')) + list((work / 'gen').rglob('*.java'))
    run(args.jdk / 'bin/javac.exe', '-encoding', 'UTF-8', '--release', '8', '-classpath', platform, '-d', work / 'classes', *sources)
    run(args.jdk / 'bin/jar.exe', 'cf', work / 'classes.jar', '-C', work / 'classes', '.')
    run(java, '-cp', tools / 'lib/d8.jar', 'com.android.tools.r8.D8', '--release', '--min-api', '26', '--lib', platform, '--output', work / 'dex', work / 'classes.jar')
    with zipfile.ZipFile(work / 'resources.apk') as base, zipfile.ZipFile(work / 'unsigned.apk', 'w') as apk:
        for entry in base.infolist(): apk.writestr(entry, base.read(entry.filename))
        for dex in (work / 'dex').glob('*.dex'): apk.write(dex, dex.name, compress_type=zipfile.ZIP_DEFLATED)
        for name in sorted([*asset_manifest['files'], 'asset-manifest.json']):
            apk.write(frontend / name, 'assets/frontend/' + name, compress_type=zipfile.ZIP_DEFLATED)
    run(tools / 'zipalign.exe', '-f', '-p', '4', work / 'unsigned.apk', work / 'aligned.apk')
    signing = CLIENT / 'signing'
    signing.mkdir(exist_ok=True)
    password_path = signing / 'android-password.txt'
    key = signing / 'xtyf-manager.jks'
    if key.exists() and not password_path.exists(): raise RuntimeError('Existing signing key has no password file; restore the original password.')
    if not password_path.exists(): password_path.write_text(secrets.token_urlsafe(36), encoding='ascii')
    env['XTYF_KEY_PASSWORD'] = password_path.read_text(encoding='ascii').strip()
    if not key.exists():
        run(args.jdk / 'bin/keytool.exe', '-genkeypair', '-keystore', key, '-storetype', 'JKS', '-alias', 'xtyf-manager', '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000', '-storepass:env', 'XTYF_KEY_PASSWORD', '-keypass:env', 'XTYF_KEY_PASSWORD', '-dname', 'CN=XTYF Manager, OU=Team, O=XTYF, C=CN')
    apk = out / f'XTYF-Manager-{config["version"]}.apk'
    run(java, '-jar', tools / 'lib/apksigner.jar', 'sign', '--ks', key, '--ks-key-alias', 'xtyf-manager', '--ks-pass', 'env:XTYF_KEY_PASSWORD', '--key-pass', 'env:XTYF_KEY_PASSWORD', '--v4-signing-enabled', 'false', '--out', apk, work / 'aligned.apk')
    run(java, '-jar', tools / 'lib/apksigner.jar', 'verify', '--verbose', '--print-certs', apk)
    run(tools / 'zipalign.exe', '-c', '-v', '4', apk)
    print(f'APK: {apk}\nSHA256: {hashlib.sha256(apk.read_bytes()).hexdigest()}')

if __name__ == '__main__': main()
