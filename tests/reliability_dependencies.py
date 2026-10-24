"""Read-only public dependency inventory and optional OSV lookup; never installs."""
import argparse
import importlib.metadata
import json
import re
import sys
import tomllib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inventory():
    rows = []
    def add(name, version, ecosystem, source):
        rows.append(dict(name=name, version=version, ecosystem=ecosystem, source=source))
    for relative in ('requirements.txt', 'requirements-dev.txt'):
        for line in (ROOT / relative).read_text(encoding='utf-8').splitlines():
            found = re.match(r'^([\w.-]+)==([^;\s]+)', line)
            if found:
                add(*found.groups(), 'PyPI', relative)
    # Actual resolver environment is recorded separately from project declarations.
    for package in importlib.metadata.distributions():
        add(package.metadata['Name'], package.version, 'PyPI', 'verification-python-environment')
    relative = 'clients/desktop/package-lock.json'
    for path, value in json.loads((ROOT / relative).read_text(encoding='utf-8'))['packages'].items():
        if path and value.get('version'):
            add(value.get('name') or path.rsplit('node_modules/', 1)[-1], value['version'], 'npm', relative)
    for relative in ('clients/frontend/vendor-lock.json', 'static/vendor/asset-tools/lock.json'):
        data = json.loads((ROOT / relative).read_text(encoding='utf-8'))
        for name, value in data.get('packages', data).items():
            add(name, value['version'], 'npm', relative)
    relative = 'clients/desktop/Cargo.lock'
    for value in tomllib.loads((ROOT / relative).read_text(encoding='utf-8'))['package']:
        if value.get('source', '').startswith('registry+'):
            add(value['name'], value['version'], 'crates.io', relative)
    return sorted(rows, key=lambda row: (row['ecosystem'], row['name'], row['version'], row['source']))


def scan(rows):
    unique = sorted({(r['ecosystem'], r['name'], r['version']) for r in rows})
    result = {'service': 'https://api.osv.dev/v1/querybatch', 'requested': len(unique), 'checked': 0,
              'matches': [], 'status': 'incomplete'}
    for start in range(0, len(unique), 100):
        batch = unique[start:start + 100]
        body = {'queries': [{'package': {'ecosystem': eco, 'name': name}, 'version': version}
                            for eco, name, version in batch]}
        try:
            request = urllib.request.Request(result['service'], data=json.dumps(body).encode(),
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=20) as response:
                answers = json.load(response)['results']
            if len(answers) != len(batch):
                raise ValueError('OSV response count mismatch')
            for identity, answer in zip(batch, answers):
                if answer.get('vulns'):
                    result['matches'].append({'package': identity, 'advisories': [v['id'] for v in answer['vulns']]})
            result['checked'] += len(batch)
        except (OSError, ValueError, KeyError) as exc:
            result['error'] = type(exc).__name__ + ': ' + str(exc)
            return result
    result['status'] = 'completed'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scan', action='store_true')
    args = parser.parse_args()
    rows = inventory()
    report = {'at_utc': datetime.now(timezone.utc).isoformat(), 'python': sys.version.split()[0],
              'inventory': rows, 'limitations': ['OS/browser/WebView/JDK vulnerabilities require platform inventory',
              'Git/path dependencies and vendored code are not proven safe by a registry lookup',
              'Verification environment is not a production Python lockfile']}
    if args.scan:
        report['scan'] = scan(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'entries': len(rows), 'scan': report.get('scan')}, ensure_ascii=False))
    return 0 if not args.scan or report['scan']['status'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
