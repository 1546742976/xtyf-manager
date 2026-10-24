"""Local UI rendering parity and packaged-asset contract checks."""
import hashlib
import base64
import io
import tarfile
from unittest import mock
import html
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from build_frontend import build,adapt_template,adapt_script
from frontend import vendor_dependencies as vendor
from jinja2 import DictLoader,Environment


class FrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.dist=Path(cls.temp.name)
        cls.manifest=build(cls.dist)
        cls.templates={p.name:adapt_template(p.name,p.read_text(encoding='utf-8-sig')) for p in (ROOT.parent/'templates').glob('*.html')}

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_all_pages_match_server_jinja_for_roles_themes_and_password_gate(self):
        urls={'index':'/','tasks':'/tasks','account':'/account','manage':'/manage','members':'/members','login':'/login','logout':'/logout','export_csv':'/export.csv','export_members_csv':'/api/members/export.csv','discussions.index':'/discussions/','security.security_page':'/admin/security','site_updates.timeline':'/updates','site_updates.version':'/api/site-release','site_updates.history':'/api/site-release/history'}
        from urllib.parse import urlencode
        def url_for(name,**options):
            if name=='static':return '/static/'+options['filename']
            return urls[name]+('?' + urlencode(options) if options else '')
        env=Environment(loader=DictLoader(self.templates),autoescape=True)
        env.policies['json.dumps_kwargs']={'ensure_ascii':False,'sort_keys':True}
        env.globals.update(url_for=url_for,csrf_token=lambda:'')
        fixtures=[]
        for role in ('member','manager','admin'):
            for dark in (False,True):
                for forced in (False,True):
                    user={'id':42,'name':'王<&"同学','username':'tester','role':role,'group_name':'电控','must_change_password':forced,'ui_theme':'dark' if dark else 'light','theme_mode':'system','light_theme':'paw','dark_theme':'xueli'}
                    context={'me':user,'role_names':{'admin':'管理员','manager':'项管','member':'成员'},'statuses':['未开始','进行中','已完成'],'priorities':['高','中','低'],'can_review_funds':role=='admin' and not forced,'can_review':role=='admin' and not forced,'is_finance':role=='admin' and not forced,'can_manage_invoices':role=='admin' and not forced,'page':'tasks','task_type':'daily' if dark else 'vehicle','project_id':'12' if dark else ''}
                    for name in self.templates:
                        fixtures.append({'name':name,'context':context,'expected':env.get_template(name).render(**context)})
        context={'me':None,'page':'login','role_names':{}}
        fixtures.append({'name':'login.html','context':context,'expected':env.get_template('login.html').render(**context)})
        script=(self.dist/'templates.js').read_text(encoding='utf-8')+'\nconst fs=require("node:fs");const tests=JSON.parse(fs.readFileSync(0,"utf8"));process.stdout.write(JSON.stringify(tests.map(t=>XTYFTemplates.render(t.name,t.context))));'
        program=self.dist/'render-check.cjs';program.write_text(script,encoding='utf-8')
        result=subprocess.run(['node',str(program)],input=json.dumps(fixtures,ensure_ascii=False),text=True,encoding='utf-8',capture_output=True,check=True)
        rendered=json.loads(result.stdout)
        for fixture,actual in zip(fixtures,rendered):
            with self.subTest(name=fixture['name'],role=fixture['context'].get('me')):
                self.assertEqual(html.unescape(actual).strip(),html.unescape(fixture['expected']).strip())

    def test_manifest_has_every_packaged_asset_and_hashes(self):
        self.assertEqual(self.manifest['templates'], sorted(self.templates))
        for name,expected in self.manifest['files'].items():
            self.assertEqual(hashlib.sha256((self.dist/name).read_bytes()).hexdigest(),expected,name)
        for name in ('index.html','page-runtime.js','external.html','vendor/bootstrap/bootstrap.min.css','vendor/bootstrap-icons/fonts/bootstrap-icons.woff2','vendor/pdfjs/pdf.mjs','vendor/pdfjs/pdf.worker.mjs'):
            self.assertIn(name,self.manifest['files'])

    def test_public_bootstrap_is_not_duplicated_but_asset_scanner_remains_packaged(self):
        lock = json.loads((ROOT/'frontend/vendor-lock.json').read_text(encoding='utf-8'))
        for name, digest in lock['files'].items():
            if name.startswith(('bootstrap/', 'bootstrap-icons/')):
                self.assertEqual(hashlib.sha256((ROOT.parent/'static/vendor'/name).read_bytes()).hexdigest(), digest, name)
        self.assertNotIn('static/vendor/bootstrap/bootstrap.min.css', self.manifest['files'])
        self.assertNotIn('static/vendor/bootstrap-icons/bootstrap-icons.min.css', self.manifest['files'])
        self.assertIn('static/vendor/asset-tools/zxing.js', self.manifest['files'])
        self.assertIn('static/vendor/asset-tools/barcode.js', self.manifest['files'])

    def test_templates_have_no_remote_scripts_styles_or_security_shims(self):
        for name,template in self.templates.items():
            self.assertNotRegex(template,r'<(?:script|link)\b[^>]*(?:src|href)=["\']https?://',name)
        self.assertNotIn('security.js',self.templates['_security_head.html'])
        self.assertNotIn('new EventSource',(self.dist/'static/invoices.js').read_text(encoding='utf-8'))
        self.assertIn('new XTYFJobPoller',(self.dist/'static/invoices.js').read_text(encoding='utf-8'))

    def test_navigation_adaptation_preserves_unrelated_identifiers(self):
        source="location.assign('/');window.location.href='/';other.location.href='/';history.replaceState(null,'','/');"
        result=adapt_script(source)
        self.assertIn('window.XTYFLocation.assign',result)
        self.assertIn('window.XTYFLocation.href',result)
        self.assertIn('other.location.href',result)
        self.assertIn('window.XTYFHistory.replaceState',result)



class VendorRestoreTests(unittest.TestCase):
    def test_missing_assets_are_verified_and_cached_without_repeated_download(self):
        payload = b'license fixture'
        archive_io = io.BytesIO()
        with tarfile.open(fileobj=archive_io, mode='w:gz') as archive:
            member = tarfile.TarInfo('package/LICENSE')
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        archive_bytes = archive_io.getvalue()
        lock = {'packages': {'bootstrap': {
            'version': '5.3.3',
            'source': 'https://registry.npmjs.org/bootstrap/-/bootstrap-5.3.3.tgz',
            'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(archive_bytes).digest()).decode()
        }}, 'files': {'bootstrap/LICENSE': hashlib.sha256(payload).hexdigest()}}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'vendor-lock.json').write_text(json.dumps(lock), encoding='utf-8')
            with mock.patch.object(vendor, 'HERE', root), mock.patch.object(
                vendor.urllib.request, 'urlopen', return_value=io.BytesIO(archive_bytes)
            ) as download:
                vendor.ensure_vendor_assets()
                vendor.ensure_vendor_assets()
                self.assertEqual(download.call_count, 1)
            self.assertEqual((root / 'vendor/bootstrap/LICENSE').read_bytes(), payload)
            (root / 'vendor/bootstrap/LICENSE').write_bytes(b'corrupted')
            with mock.patch.object(vendor, 'HERE', root), mock.patch.object(
                vendor.urllib.request, 'urlopen', side_effect=AssertionError('unwanted download')
            ):
                with self.assertRaisesRegex(ValueError, 'Vendor checksum mismatch'):
                    vendor.ensure_vendor_assets()

    def test_invalid_tarball_integrity_never_writes_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'vendor-lock.json').write_text(json.dumps({
                'packages': {'bootstrap': {
                    'version': '5.3.3',
                    'source': 'https://registry.npmjs.org/bootstrap/-/bootstrap-5.3.3.tgz',
                    'integrity': 'sha512-invalid'
                }},
                'files': {'bootstrap/LICENSE': hashlib.sha256(b'license').hexdigest()}
            }), encoding='utf-8')
            with mock.patch.object(vendor, 'HERE', root), mock.patch.object(
                vendor.urllib.request, 'urlopen', return_value=io.BytesIO(b'invalid')
            ):
                with self.assertRaisesRegex(ValueError, 'npm tarball integrity mismatch'):
                    vendor.ensure_vendor_assets()
            self.assertFalse((root / 'vendor/bootstrap/LICENSE').exists())


if __name__=='__main__':unittest.main()
