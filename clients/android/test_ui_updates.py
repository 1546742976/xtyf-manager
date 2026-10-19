"""Compile and run native Android update tests; never build or install an APK."""
from pathlib import Path
import argparse
import hashlib
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
JSON_SHA256 = '3cf6cd6892e32e2b4c1c39e0f52f5248a2f5b37646fdfbb79a66b46b618414ed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jdk', type=Path, default=next((ROOT / '.build-tools/jdk21').glob('jdk-*'), None))
    parser.add_argument('--json-jar', type=Path, default=ROOT / '.build-tools/client-ui-updates/android-tests/json-20240303.jar')
    parser.add_argument('--android-jar', type=Path, default=ROOT / '.build-tools/android/android-36/android.jar')
    parser.add_argument('--fixture', type=Path, help='Optional output of tests/client_update_fixture.py')
    args = parser.parse_args()
    if not args.jdk or not args.json_jar.is_file():
        parser.error('JDK and official org.json:json:20240303 test-only JAR are required')
    if hashlib.sha256(args.json_jar.read_bytes()).hexdigest() != JSON_SHA256:
        parser.error('org.json test-only JAR SHA256 mismatch')
    suffix = '.exe' if os.name == 'nt' else ''
    java, javac = (args.jdk / ('bin/' + tool + suffix) for tool in ('java', 'javac'))
    source = ROOT / 'clients/android/src/online/skywalker/manager'
    with tempfile.TemporaryDirectory(prefix='xtyf-ui-update-test-') as temporary:
        subprocess.run([str(javac), '-encoding', 'UTF-8', '--release', '8', '-cp', str(args.json_jar), '-d', temporary,
                        str(source / 'NativeHttpClient.java'), str(source / 'UiUpdateStore.java'),
                        str(ROOT / 'clients/android/tests/UiUpdateStoreTest.java')], check=True)
        subprocess.run([str(java), '-cp', temporary + os.pathsep + str(args.json_jar),
                        'online.skywalker.manager.UiUpdateStoreTest', *([str(args.fixture)] if args.fixture else [])], check=True, timeout=60)
        if args.android_jar.is_file():
            build_config = Path(temporary) / 'BuildConfig.java'
            build_config.write_text('package online.skywalker.manager; public class BuildConfig { '
                                    'public static final String VERSION="1.0.10", SERVER_URL="http://localhost"; }', encoding='utf-8')
            subprocess.run([str(javac), '-encoding', 'UTF-8', '--release', '8', '-cp', str(args.android_jar),
                            '-d', temporary, *map(str, source.glob('*.java')), str(build_config)], check=True)
            print('Android Activity + transport + update store compiled against Android SDK (no APK)')


if __name__ == '__main__':
    main()
