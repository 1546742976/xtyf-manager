"""Maintainer-only download of pinned upstream browser dependencies.

Normal builds reuse a local verified cache; the first build downloads exactly
locked tarballs, checks SHA-512 and per-file SHA-256, then packages offline assets.
Tar members are selected explicitly; archives are never extracted to disk.
"""
import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

HERE=Path(__file__).resolve().parent
PACKAGES={"bootstrap":"5.3.3","bootstrap-icons":"1.11.3","pdfjs-dist":"5.4.624"}


def vendor():
    lock={"packages":{},"files":{}}
    for package,version in PACKAGES.items():
        with urllib.request.urlopen(f"https://registry.npmjs.org/{package}/{version}",timeout=90) as response:
            metadata=json.load(response)
        dist=metadata['dist']
        with urllib.request.urlopen(dist['tarball'],timeout=180) as response:data=response.read()
        algorithm,expected=dist['integrity'].split('-',1)
        if algorithm!='sha512' or base64.b64encode(hashlib.sha512(data).digest()).decode()!=expected:
            raise ValueError('npm tarball integrity mismatch: '+package)
        lock['packages'][package]={'version':version,'source':dist['tarball'],'integrity':dist['integrity'],'license':metadata.get('license','')}
        with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
            for member in archive.getmembers():
                source=member.name;target=None
                if package=='bootstrap':
                    exact={'package/dist/css/bootstrap.min.css':'bootstrap/bootstrap.min.css','package/dist/js/bootstrap.bundle.min.js':'bootstrap/bootstrap.bundle.min.js','package/LICENSE':'bootstrap/LICENSE'}
                    target=exact.get(source)
                elif package=='bootstrap-icons':
                    if source=='package/font/bootstrap-icons.min.css':target='bootstrap-icons/bootstrap-icons.min.css'
                    elif source.startswith('package/font/fonts/') and source.endswith(('.woff','.woff2')):target='bootstrap-icons/fonts/'+Path(source).name
                    elif source=='package/LICENSE':target='bootstrap-icons/LICENSE'
                else:
                    if source in ('package/legacy/build/pdf.mjs','package/legacy/build/pdf.worker.mjs'):target='pdfjs/'+Path(source).name
                    elif source.startswith(('package/cmaps/','package/standard_fonts/','package/wasm/')):target='pdfjs/'+source.removeprefix('package/')
                    elif source=='package/LICENSE':target='pdfjs/LICENSE'
                if not target or not member.isfile():continue
                content=archive.extractfile(member).read();destination=HERE/'vendor'/target
                if not destination.resolve().is_relative_to((HERE/'vendor').resolve()):raise ValueError('Invalid archive path')
                destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(content)
                lock['files'][target]=hashlib.sha256(content).hexdigest()
        print(package,version,'verified',flush=True)
    (HERE/'vendor-lock.json').write_text(json.dumps(lock,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')



def _target_for(package, source):
    """Map the small, explicitly allowed subset of npm archives into local assets."""
    if package == 'bootstrap':
        return {'package/dist/css/bootstrap.min.css':'bootstrap/bootstrap.min.css',
                'package/dist/js/bootstrap.bundle.min.js':'bootstrap/bootstrap.bundle.min.js',
                'package/LICENSE':'bootstrap/LICENSE'}.get(source)
    if package == 'bootstrap-icons':
        if source == 'package/font/bootstrap-icons.min.css':
            return 'bootstrap-icons/bootstrap-icons.min.css'
        if source.startswith('package/font/fonts/') and source.endswith(('.woff', '.woff2')):
            return 'bootstrap-icons/fonts/' + Path(source).name
        if source == 'package/LICENSE':
            return 'bootstrap-icons/LICENSE'
    if package == 'pdfjs-dist':
        if source in ('package/legacy/build/pdf.mjs', 'package/legacy/build/pdf.worker.mjs'):
            return 'pdfjs/' + Path(source).name
        if source.startswith(('package/cmaps/', 'package/standard_fonts/', 'package/wasm/')):
            return 'pdfjs/' + source.removeprefix('package/')
        if source == 'package/LICENSE':
            return 'pdfjs/LICENSE'
    return None


def ensure_vendor_assets():
    """Restore missing dependencies; never accept wrong cache bytes or unpinned downloads.

    The packaged application remains fully offline. Only build machines fetch
    these dependencies once, and the exact bytes are verified before packaging.
    """
    import os
    import tempfile
    lock = json.loads((HERE / 'vendor-lock.json').read_text(encoding='utf-8'))
    target_root = (HERE / 'vendor').resolve()
    missing = set()
    for name, digest in lock['files'].items():
        path = (target_root / name).resolve()
        if not path.is_relative_to(target_root):
            raise ValueError('Unsafe vendor file path: ' + name)
        if path.is_file():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('Vendor checksum mismatch: ' + name)
        else:
            missing.add(name)
    if not missing:
        return
    for package, pinned in lock['packages'].items():
        prefix = 'pdfjs/' if package == 'pdfjs-dist' else package + '/'
        requested = {name for name in missing if name.startswith(prefix)}
        if not requested:
            continue
        source = pinned['source']
        if not source.startswith('https://registry.npmjs.org/' + package + '/-/'):
            raise ValueError('Vendor tarball origin must be npm registry: ' + package)
        print('Restoring verified vendor assets:', package, pinned['version'], flush=True)
        with urllib.request.urlopen(source, timeout=180) as response:
            data = response.read(128 * 1024 * 1024 + 1)
        if len(data) > 128 * 1024 * 1024:
            raise ValueError('Vendor tarball too large: ' + package)
        algorithm, expected = pinned['integrity'].split('-', 1)
        if algorithm != 'sha512' or base64.b64encode(hashlib.sha512(data).digest()).decode() != expected:
            raise ValueError('npm tarball integrity mismatch: ' + package)
        pending = {}
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            for member in archive:
                name = _target_for(package, member.name)
                if name not in requested:
                    continue
                if not member.isfile() or member.size > 16 * 1024 * 1024 or name in pending:
                    raise ValueError('Invalid or duplicate vendor asset: ' + name)
                content = archive.extractfile(member).read()
                if hashlib.sha256(content).hexdigest() != lock['files'][name]:
                    raise ValueError('Vendor checksum mismatch: ' + name)
                pending[name] = content
        if set(pending) != requested:
            raise ValueError('Missing pinned vendor resources: ' + ', '.join(sorted(requested - set(pending))))
        for name, content in pending.items():
            destination = target_root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.vendor-', dir=destination.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(content)
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        missing.difference_update(requested)
    if missing:
        raise ValueError('Unmapped vendor resources: ' + ', '.join(sorted(missing)))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-lock', action='store_true', help='Maintainer only: re-pin current PACKAGES and overwrite vendor-lock.json')
    args = parser.parse_args()
    vendor() if args.refresh_lock else ensure_vendor_assets()
