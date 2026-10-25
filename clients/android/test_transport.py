"""Compile and exercise the Android native HTTP client without Android or network accounts."""
from pathlib import Path
import argparse
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jdk', type=Path, default=next((ROOT / '.build-tools/jdk21').glob('jdk-*'), None))
    args = parser.parse_args()
    suffix = '.exe' if __import__('os').name == 'nt' else ''
    javac = args.jdk / ('bin/javac' + suffix) if args.jdk else shutil.which('javac')
    java = args.jdk / ('bin/java' + suffix) if args.jdk else shutil.which('java')
    if not javac or not java:
        parser.error('A JDK is required; provide --jdk')
    with tempfile.TemporaryDirectory(prefix='xtyf-android-http-') as temporary:
        subprocess.run([str(javac), '-encoding', 'UTF-8', '-d', temporary,
                        str(ROOT / 'clients/android/src/online/skywalker/manager/NativeHttpClient.java'),
                        str(ROOT / 'clients/android/src/online/skywalker/manager/WebsitePolicy.java'),
                        str(ROOT / 'clients/android/src/online/skywalker/manager/PdfSave.java'),
                        str(ROOT / 'clients/android/tests/NativeHttpClientTest.java')], check=True)
        subprocess.run([str(java), '-cp', temporary, 'online.skywalker.manager.NativeHttpClientTest'], check=True, timeout=30)


if __name__ == '__main__':
    main()
