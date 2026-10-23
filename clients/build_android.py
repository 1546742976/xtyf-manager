"""Build a signed APK with the official JDK and Android SDK, without Gradle."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / 'clients'
SOURCE = CLIENT / 'android'


def resolve_signing_identity(signing_dir, allow_create=False, *, keytool=None):
    """Never replace a missing half of an existing release identity."""
    signing_dir = Path(signing_dir)
    key = signing_dir / 'xtyf-manager.jks'
    password_path = signing_dir / 'android-password.txt'
    if key.is_file() and password_path.is_file():
        if not password_path.read_text(encoding='ascii').strip():
            raise RuntimeError('签名密码为空，请恢复原始签名凭证。')
        return key, password_path
    if key.exists() or password_path.exists():
        raise RuntimeError('签名密钥或密码文件缺失，请恢复配套凭证；不能重新生成替代。')
    if not allow_create or keytool is None:
        raise RuntimeError('缺少 Android 发布签名凭证，请恢复备份；新项目须显式确认初始化。')
    signing_dir.mkdir(parents=True, exist_ok=True)
    # Generate in a private temporary directory before publishing either file.
    with tempfile.TemporaryDirectory(prefix='new-signing-', dir=signing_dir) as directory:
        temporary_key = Path(directory) / key.name
        password = secrets.token_urlsafe(36)
        env = dict(os.environ, XTYF_KEY_PASSWORD=password)
        subprocess.run([str(keytool), '-genkeypair', '-keystore', str(temporary_key),
                        '-storetype', 'JKS', '-alias', 'xtyf-manager', '-keyalg', 'RSA',
                        '-keysize', '3072', '-validity', '10000',
                        '-storepass:env', 'XTYF_KEY_PASSWORD', '-keypass:env', 'XTYF_KEY_PASSWORD',
                        '-dname', 'CN=XTYF Manager, OU=Team, O=XTYF, C=CN'],
                       check=True, env=env, capture_output=True)
        with password_path.open('x', encoding='ascii') as stream:
            stream.write(password)
        try:
            # Exclusive creation prevents concurrent initialization replacing a key.
            with key.open('xb') as stream:
                stream.write(temporary_key.read_bytes())
        except BaseException:
            password_path.unlink()
            raise
    return key, password_path


def parse_certificate_sha256(output):
    values = re.findall(r'^Signer #\d+ certificate SHA-256 digest:\s*([0-9a-fA-F:]+)\s*$', output, re.M)
    if len(values) != 1 or not re.fullmatch(r'[0-9a-f]{64}', values[0].replace(':', '').lower()):
        raise RuntimeError('无法确认唯一的 Android 签名证书 SHA-256。')
    return values[0].replace(':', '').lower()


def collect_apk_metadata(apk, java, tools, env):
    verified = subprocess.run([str(java), '-jar', str(tools / 'lib/apksigner.jar'),
                               'verify', '--verbose', '--print-certs', str(apk)],
                              check=True, env=env, capture_output=True)
    align = tools / ('zipalign.exe' if os.name == 'nt' else 'zipalign')
    subprocess.run([str(align), '-c', '-v', '4', str(apk)], check=True, env=env,
                   capture_output=True)
    # SDK tools may print localized Windows paths. The certificate field itself
    # is ASCII; do not let unrelated console encoding break signature checks.
    return {'certificateSha256': parse_certificate_sha256(verified.stdout.decode('utf-8', errors='replace')),
            'signatureVerified': True, 'alignmentVerified': True,
            'apkSha256': hashlib.sha256(apk.read_bytes()).hexdigest()}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jdk', type=Path, default=(Path(os.environ['JAVA_HOME']) if os.environ.get('JAVA_HOME') else next((ROOT / '.build-tools/jdk21').glob('jdk-*'), None)))
    parser.add_argument('--sdk', type=Path, default=(Path(os.environ['ANDROID_HOME']) if os.environ.get('ANDROID_HOME') else ROOT / '.build-tools/android'))
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist/clients/android')
    parser.add_argument('--skip-frontend-build', action='store_true', help='Use the shared frontend already built by release.py')
    signing_options = parser.add_mutually_exclusive_group()
    signing_options.add_argument('--init-signing-key', action='store_true', help='显式初始化新的发布签名，另需 XTYF_CONFIRM_NEW_SIGNING_KEY=1')
    signing_options.add_argument('--test-signing', action='store_true', help='仅使用本次临时测试签名，不可作为正式发布')
    args = parser.parse_args()
    if not args.jdk:
        parser.error('Provide --jdk pointing to a JDK 17 or newer')
    # Use the *exact* SDK components declared by this project, on both Windows
    # and Linux hosted Actions runners. Never accidentally select API 35/older.
    extension = '.exe' if os.name == 'nt' else ''
    tools = args.sdk / 'build-tools' / '36.0.0'
    tool = tools / ('aapt2' + extension)
    platform = args.sdk / 'platforms' / 'android-36' / 'android.jar'
    if not tool.is_file() or not platform.is_file():
        parser.error('Provide --sdk containing Android build-tools 36.0.0 and platform android-36')
    java = args.jdk / 'bin' / ('java' + extension)
    env = dict(os.environ, JAVA_HOME=str(args.jdk))
    if args.init_signing_key and os.environ.get('XTYF_CONFIRM_NEW_SIGNING_KEY') != '1':
        parser.error('初始化签名须显式设置 XTYF_CONFIRM_NEW_SIGNING_KEY=1；请先确认不是遗失原密钥。')
    # Test identity lives outside the persistent release-signing directory.
    test_identity = tempfile.TemporaryDirectory(prefix='xtyf-test-signing-') if args.test_signing else None
    try:
        key, password_path = resolve_signing_identity(
            Path(test_identity.name) if test_identity else Path(os.environ.get('XTYF_SIGNING_DIR', str(CLIENT / 'signing'))),
            allow_create=args.init_signing_key or args.test_signing,
            keytool=args.jdk / 'bin' / ('keytool' + extension))
    except BaseException:
        if test_identity:
            test_identity.cleanup()
        raise
    env['XTYF_KEY_PASSWORD'] = password_path.read_text(encoding='ascii').strip()
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
    update_public_key = CLIENT / 'update-public-key.pem'
    if not update_public_key.is_file():
        raise RuntimeError('Pinned UI update public key is missing')
    asset_manifest = json.loads((frontend / 'asset-manifest.json').read_text(encoding='utf-8'))
    if asset_manifest.get('version') != config['version']:
        raise RuntimeError('Shared frontend version does not match the Android build version')
    for name, digest in asset_manifest['files'].items():
        asset = (frontend / name).resolve()
        if not asset.is_relative_to(frontend.resolve()) or not asset.is_file() or hashlib.sha256(asset.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Shared frontend asset is missing or changed: {name}')
    out = args.output_dir.resolve()
    # The raster launcher icon is generated from the checked-in borderless SVG.
    subprocess.run([sys.executable, str(CLIENT / 'build_icons.py')], cwd=ROOT, check=True)
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
    run(args.jdk / 'bin' / ('javac' + extension), '-encoding', 'UTF-8', '--release', '8', '-classpath', platform, '-d', work / 'classes', *sources)
    run(args.jdk / 'bin' / ('jar' + extension), 'cf', work / 'classes.jar', '-C', work / 'classes', '.')
    run(java, '-cp', tools / 'lib/d8.jar', 'com.android.tools.r8.D8', '--release', '--min-api', '26', '--lib', platform, '--output', work / 'dex', work / 'classes.jar')
    with zipfile.ZipFile(work / 'resources.apk') as base, zipfile.ZipFile(work / 'unsigned.apk', 'w') as apk:
        apk.write(update_public_key, 'assets/update-public-key.pem', compress_type=zipfile.ZIP_DEFLATED)
        for entry in base.infolist(): apk.writestr(entry, base.read(entry.filename))
        for dex in (work / 'dex').glob('*.dex'): apk.write(dex, dex.name, compress_type=zipfile.ZIP_DEFLATED)
        for name in sorted([*asset_manifest['files'], 'asset-manifest.json']):
            apk.write(frontend / name, 'assets/frontend/' + name, compress_type=zipfile.ZIP_DEFLATED)
    run(tools / ('zipalign' + extension), '-f', '-p', '4', work / 'unsigned.apk', work / 'aligned.apk')
    apk = out / f'XTYF-Manager-{config["version"]}.apk'
    run(java, '-jar', tools / 'lib/apksigner.jar', 'sign', '--ks', key, '--ks-key-alias', 'xtyf-manager', '--ks-pass', 'env:XTYF_KEY_PASSWORD', '--key-pass', 'env:XTYF_KEY_PASSWORD', '--v4-signing-enabled', 'false', '--out', apk, work / 'aligned.apk')
    metadata = collect_apk_metadata(apk, java, tools, env)
    metadata.update(version=config['version'], versionCode=config['versionCode'], testSigning=args.test_signing)
    (out / 'android-build.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    if test_identity:
        test_identity.cleanup()
    print(f'APK: {apk}\nSHA256: {hashlib.sha256(apk.read_bytes()).hexdigest()}')

if __name__ == '__main__': main()
