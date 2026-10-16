"""Maintainer-only download of pinned upstream browser dependencies.

Normal builds use the checked-in assets and SHA-256 lock without network access.
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


if __name__=='__main__':vendor()
