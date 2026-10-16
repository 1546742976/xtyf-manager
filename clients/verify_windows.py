"""Verify the actual NSIS payload, using a full 7-Zip with NSIS support.

`--setup` only extracts pinned official tools into .build-tools/7zip; it never
installs an MSI or changes Windows registration. XTYF_7ZIP can select a separately
managed full 7z.exe. The limited 7za/7zr builds cannot inspect NSIS packages.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path,PureWindowsPath
import shutil
import struct
import subprocess
import tempfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
TOOLS=ROOT/'.build-tools/7zip'
SEVENZIP_VERSION='26.04'
SEVENZIP_URL='https://www.7-zip.org/a/7z2604-x64.msi'
SEVENZIP_SHA256='0b01334a418654293513449f61d6bfc99e5196ca371e3c3dc961eda57cd535c6'


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def run(args,timeout=180):
    result=subprocess.run([str(a) for a in args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        error=(result.stdout+result.stderr).decode('utf-8',errors='replace')[-5000:]
        raise RuntimeError(f"Command failed ({result.returncode}): {args[0]}\n{error}")
    return result.stdout.decode('utf-8',errors='replace')


def extract_msi_cabinet(msi_path,cabinet_path):
    """Read the known cabinet stream through Microsoft's read-only MSI API."""
    if os.name!='nt':raise RuntimeError('7-Zip setup requires Windows; use XTYF_7ZIP on another host.')
    msi=ctypes.WinDLL('msi')
    handle=wintypes.UINT
    msi.MsiOpenDatabaseW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR,ctypes.POINTER(handle)]
    msi.MsiDatabaseOpenViewW.argtypes=[handle,wintypes.LPCWSTR,ctypes.POINTER(handle)]
    msi.MsiViewExecute.argtypes=[handle,handle]
    msi.MsiViewFetch.argtypes=[handle,ctypes.POINTER(handle)]
    msi.MsiRecordReadStream.argtypes=[handle,wintypes.UINT,ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD)]
    msi.MsiCloseHandle.argtypes=[handle]
    database,view,record=handle(),handle(),handle()
    def checked(code):
        if code:raise OSError(code,'Failed to read official 7-Zip MSI cabinet')
    try:
        checked(msi.MsiOpenDatabaseW(str(msi_path),None,ctypes.byref(database)))
        checked(msi.MsiDatabaseOpenViewW(database,"SELECT `Data` FROM `_Streams` WHERE `Name`='product.cab'",ctypes.byref(view)))
        checked(msi.MsiViewExecute(view,0));checked(msi.MsiViewFetch(view,ctypes.byref(record)))
        with Path(cabinet_path).open('wb') as stream:
            while True:
                buffer=ctypes.create_string_buffer(65536);size=wintypes.DWORD(len(buffer))
                checked(msi.MsiRecordReadStream(record,1,buffer,ctypes.byref(size)))
                if not size.value:break
                stream.write(buffer.raw[:size.value])
    finally:
        for current in (record,view,database):
            if current.value:msi.MsiCloseHandle(current)


def setup():
    TOOLS.mkdir(parents=True,exist_ok=True)
    package=TOOLS/'7z2604-x64.msi'
    if not package.is_file() or sha256(package)!=SEVENZIP_SHA256:
        with urllib.request.urlopen(SEVENZIP_URL,timeout=120) as response:data=response.read()
        if hashlib.sha256(data).hexdigest()!=SEVENZIP_SHA256:raise ValueError('Official 7-Zip MSI SHA-256 mismatch')
        package.write_bytes(data)
    with tempfile.TemporaryDirectory(prefix='setup-',dir=TOOLS) as staging:
        staging=Path(staging);cabinet=staging/'product.cab';extract_msi_cabinet(package,cabinet)
        expand=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/expand.exe'
        for member,target in [('_7z.exe','7z.exe'),('_7z.dll','7z.dll'),('License.txt','License.txt')]:
            run([expand,'-F:'+member,cabinet,staging])
            source=staging/member
            if not source.is_file():raise RuntimeError('Official cabinet is missing '+member)
            shutil.copyfile(source,TOOLS/target)
    metadata={'version':SEVENZIP_VERSION,'source':SEVENZIP_URL,'sha256':SEVENZIP_SHA256,
              'files':{name:sha256(TOOLS/name) for name in ('7z.exe','7z.dll','License.txt')}}
    (TOOLS/'setup.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    return find_extractor()


def find_extractor():
    explicit=os.environ.get('XTYF_7ZIP')
    candidates=[Path(explicit)] if explicit else [TOOLS/'7z.exe']
    if not explicit:
        if shutil.which('7z'):candidates.append(Path(shutil.which('7z')))
        if os.environ.get('ProgramFiles'):candidates.append(Path(os.environ['ProgramFiles'])/'7-Zip/7z.exe')
    failures=[]
    for candidate in candidates:
        if not candidate.is_file():continue
        try:
            if candidate.resolve()==(TOOLS/'7z.exe').resolve():
                metadata=json.loads((TOOLS/'setup.json').read_text(encoding='utf-8'))
                for name,expected in metadata['files'].items():
                    if sha256(TOOLS/name)!=expected:raise ValueError('Cached 7-Zip tool checksum mismatch: '+name)
            info=run([candidate,'i'],timeout=30)
            if not any(' Nsis ' in line for line in info.splitlines()):raise ValueError('This 7-Zip lacks NSIS decoding')
            return candidate
        except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired) as error:failures.append(str(error))
    detail='\n'.join(failures)
    raise RuntimeError('Full 7-Zip with NSIS support is required. Run python clients/verify_windows.py --setup, or set XTYF_7ZIP to a full 7z.exe.\n'+detail)


def pe_machine(path):
    with Path(path).open('rb') as stream:
        if stream.read(2)!=b'MZ':raise ValueError('Application payload is not a Windows PE executable')
        stream.seek(0x3c);header=struct.unpack('<I',stream.read(4))[0];stream.seek(header)
        if stream.read(4)!=b'PE\0\0':raise ValueError('Application payload has an invalid PE header')
        return struct.unpack('<H',stream.read(2))[0]


def verify(installer,application,manifest,version,report=None):
    installer,application,manifest=(Path(p).resolve() for p in (installer,application,manifest))
    for path in (installer,application,manifest):
        if not path.is_file():raise FileNotFoundError(path)
    extractor=find_extractor()
    listing=run([extractor,'l','-slt','-sccUTF-8','--',installer])
    if 'Type = Nsis' not in listing:raise ValueError('Installer is not an NSIS archive')
    # Validate member names before extraction, even though the installer is locally built.
    files=listing.split('----------',1)[-1]
    for line in files.splitlines():
        if not line.startswith('Path = '):continue
        name=line.removeprefix('Path = ').strip();path=PureWindowsPath(name)
        if path.is_absolute() or path.drive or '..' in path.parts:raise ValueError('Unsafe NSIS archive member: '+name)
    workspace=ROOT/'.build-tools/windows-payload-check';workspace.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nsis-',dir=workspace) as temporary:
        target=Path(temporary)
        run([extractor,'x','-y','-sccUTF-8','-o'+str(target),'--',installer])
        applications=list(target.rglob('xtyf-manager.exe'));manifests=list(target.rglob('asset-manifest.json'))
        if len(applications)!=1 or len(manifests)!=1:raise ValueError(f'Unexpected NSIS payload: {len(applications)} application(s), {len(manifests)} manifest(s)')
        bundled_app,bundled_manifest=applications[0],manifests[0]
        executable_hash,manifest_hash=sha256(application),sha256(manifest)
        if sha256(bundled_app)!=executable_hash:raise ValueError('NSIS application differs from the compiled release EXE')
        if sha256(bundled_manifest)!=manifest_hash:raise ValueError('NSIS frontend manifest differs from the shared frontend build')
        if pe_machine(bundled_app)!=0x8664:raise ValueError('NSIS application is not x86_64')
        build_info=target/'extracted-build-info.json'
        run([bundled_app,'--build-info',build_info],timeout=30)
        embedded=json.loads(build_info.read_text(encoding='utf-8-sig'))
        expected=json.loads(manifest.read_text(encoding='utf-8-sig'))
        if embedded.get('version')!=version or embedded.get('framework')!='tauri' or embedded.get('architecture')!='x86_64':raise ValueError('Extracted application version/framework/architecture mismatch')
        if embedded.get('frontend')!=expected or expected.get('version')!=version:raise ValueError('Extracted executable embedded frontend manifest mismatch')
        result={'version':version,'installer':'NSIS','framework':'tauri','architecture':'x64',
                'clientPayloadVerified':True,'installerPayloadExtracted':True,'nativeExecutableVerified':True,
                'embeddedFrontendVerified':True,'runtimeUiVerified':False,
                'installerSha256':sha256(installer),'applicationSha256':executable_hash,
                'frontendManifestSha256':manifest_hash,'extractedApplication':bundled_app.relative_to(target).as_posix(),
                'extractedManifest':bundled_manifest.relative_to(target).as_posix(),'extractor':str(extractor)}
    if report:
        report=Path(report);report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup',action='store_true');parser.add_argument('--check',action='store_true')
    parser.add_argument('--installer',type=Path);parser.add_argument('--application',type=Path)
    parser.add_argument('--manifest',type=Path,default=ROOT/'clients/frontend/dist/asset-manifest.json')
    parser.add_argument('--version');parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    if args.setup:print('WINDOWS_EXTRACTOR_READY',setup());return
    if args.check:print('WINDOWS_EXTRACTOR_READY',find_extractor());return
    if not args.installer or not args.application or not args.version:parser.error('--installer, --application and --version are required')
    print(json.dumps(verify(args.installer,args.application,args.manifest,args.version,args.report),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
