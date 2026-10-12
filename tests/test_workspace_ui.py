"""Sidebar workspace A: routes, roles, CSV and template integration."""
import unittest
from test_tasks import app_module

class TestWorkspaceNavigation(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        result = self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        self.assertTrue(result.get_json()['ok'])

    def test_all_workspace_pages_render_shared_navigation(self):
        for url, page_label in [
            ('/', '工作台'), ('/tasks', '任务中心'), ('/manage', '任务管理'),
            ('/members', '成员管理'), ('/funds/', '资金台账'),
            ('/invoices/', '发票中心'), ('/account', '账户设置'),
        ]:
            with self.subTest(url=url):
                response = self.admin.get(url)
                self.assertEqual(response.status_code, 200)
                text = response.get_data(as_text=True)
                self.assertIn('workspace-sidebar', text)
                self.assertIn('workspace.css', text)
                self.assertIn('workspace.js', text)
                self.assertIn(page_label, text)

    def test_member_exports_csv_without_credentials(self):
        response = self.admin.get('/api/members/export.csv')
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.headers['Content-Disposition'])
        text = response.get_data(as_text=True)
        self.assertIn('姓名,用户名,组别,角色,账号状态', text)
        self.assertNotIn('password_hash', text)
        self.assertNotIn('ci-test-password-only', text)

    def test_csv_escapes_spreadsheet_formulas(self):
        created = self.admin.post('/api/member', json={
            'name': '=cmd()', 'username': 'formula_user',
            'password': 'formula-test-password',
            'group_name': '+danger'
        })
        self.assertTrue(created.get_json()['ok'])
        csv_text = self.admin.get('/api/members/export.csv').get_data(as_text=True)
        self.assertIn("'=cmd()", csv_text)
        self.assertIn("'+danger", csv_text)

    def test_member_cannot_access_private_management_pages(self):
        created = self.admin.post('/api/member', json={
            'name':'访客A','username':'workspace_member','password':'member-pass-123',
        })
        self.assertTrue(created.get_json()['ok'])
        member = app_module.app.test_client()
        self.assertTrue(member.post('/login',json={
            'username':'workspace_member','password':'member-pass-123',
        }).get_json()['ok'])
        for url in ('/manage','/members','/funds/'):
            with self.subTest(url=url):
                self.assertEqual(member.get(url).status_code,302)
        text = member.get('/invoices/').get_data(as_text=True)
        self.assertIn('我的发票',text)
        self.assertNotIn('href="/members"',text)
        self.assertNotIn('href="/funds/" class="ws-nav-link',text)
        self.assertEqual(member.get('/api/members/export.csv').status_code,403)
        self.assertEqual(member.get('/tasks').status_code,200)

    def test_static_workspace_assets(self):
        for name in ('workspace.css','workspace.js','dashboard.js'):
            response=self.admin.get('/static/'+name)
            self.assertEqual(response.status_code,200)
            response.close()

if __name__ == '__main__':
    unittest.main()
