"""Local UI rendering parity and packaged-asset contract checks."""
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from build_frontend import SOURCE,build,adapt_template,adapt_script
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
        urls={'index':'/','tasks':'/tasks','account':'/account','manage':'/manage','members':'/members','login':'/login','logout':'/logout','export_csv':'/export.csv','export_members_csv':'/api/members/export.csv','discussions.index':'/discussions/','security.security_page':'/admin/security'}
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
        self.assertEqual(len(self.manifest['templates']),21)
        for name,expected in self.manifest['files'].items():
            self.assertEqual(hashlib.sha256((self.dist/name).read_bytes()).hexdigest(),expected,name)
        for name in ('index.html','page-runtime.js','external.html','vendor/bootstrap/bootstrap.min.css','vendor/bootstrap-icons/fonts/bootstrap-icons.woff2','vendor/pdfjs/pdf.mjs','vendor/pdfjs/pdf.worker.mjs'):
            self.assertIn(name,self.manifest['files'])

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


if __name__=='__main__':unittest.main()
