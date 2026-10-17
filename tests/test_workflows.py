"""前端资源与关键业务流程冒烟测试（隔离于生产数据）。"""
import os
import unittest
from test_tasks import app_module

from modules.funds import get_funds_db


class TestWorkflows(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        result = self.admin.post('/login', json={
            'username': 'admin', 'password': '123456',
        })
        self.assertTrue(result.get_json()['ok'])

    def test_all_pages_load_new_assets(self):
        for url, fragment in (
            ('/', '/static/ui.css'),
            ('/manage', '/static/manage.js'),
            ('/funds/', '/static/funds.js'),
            ('/invoices/', '/static/invoices.js'),
            ('/account', '/static/account.js'),
        ):
            response = self.admin.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertIn(fragment, response.get_data(as_text=True), url)
        for asset in ('ui.css', 'funds.js', 'invoices.js', 'manage.js', 'account.js'):
            response = self.admin.get('/static/' + asset)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_account_password_validation(self):
        bad = self.admin.post('/api/password', json={
            'old': 'wrong-password', 'new': 'new-password-123',
        })
        self.assertEqual(bad.status_code, 400)

    def test_funds_invalid_and_review_permission(self):
        bad = self.admin.post('/funds/api/records', json={
            'record_type': '进', 'amount': 'NaN', 'record_date': '2026-10-01',
        })
        self.assertEqual(bad.status_code, 400)
        created = self.admin.post('/funds/api/records', json={
            'record_type': '进', 'amount': 123.5, 'record_date': '2026-10-01',
            'source_dest': '单元测试',
        })
        self.assertEqual(created.get_json()['code'], 0)
        rid = created.get_json()['id']
        unauth = self.admin.post(f'/funds/api/records/{rid}/review')
        self.assertEqual(unauth.status_code, 403)

    def test_approved_record_edit_reopens_review(self):
        created = self.admin.post('/funds/api/records', json={
            'record_type': '进', 'amount': 25, 'record_date': '2026-10-02',
        })
        self.assertEqual(created.get_json()['code'], 0)
        rid = created.get_json()['id']
        reviewer_created = self.admin.post('/api/member', json={
            'name': '财务测试审核人', 'username': 'chy',
            'password': 'ci-review-password', 'role': 'manager',
        })
        self.assertTrue(reviewer_created.get_json()['ok'])
        grant = self.admin.post(
            f"/api/admin/permissions/{reviewer_created.get_json()['id']}",
            json={"permissions": ["funds.review"]},
        )
        self.assertEqual(grant.status_code, 200, grant.get_json())
        reviewer = app_module.app.test_client()
        reviewer_login = reviewer.post('/login', json={
            'username': 'chy', 'password': 'ci-review-password',
        })
        self.assertTrue(reviewer_login.get_json()['ok'])
        approved = reviewer.post(f'/funds/api/records/{rid}/review', json={'version': created.get_json()['version']})
        self.assertEqual(approved.get_json()['code'], 0)
        edited = self.admin.put(f'/funds/api/records/{rid}',
                                json={'amount': 35, 'version': approved.get_json()['version']})
        self.assertEqual(edited.get_json()['code'], 0)
        self.assertTrue(edited.get_json()['review_required'])
        conn = get_funds_db()
        row = conn.execute('SELECT review_status, reviewed_by FROM records WHERE id=?', (rid,)).fetchone()
        conn.close()
        self.assertEqual(row['review_status'], '待审核')
        self.assertEqual(row['reviewed_by'], '')

    def test_invoice_csv_download_and_invalid_pagination(self):
        response = self.admin.get('/invoices/api/export/download')
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.headers.get('Content-Disposition', ''))
        bad = self.admin.get('/invoices/api/invoices?page=oops')
        self.assertEqual(bad.status_code, 400)
        bad_range = self.admin.get('/invoices/api/invoices?min_amount=NaN')
        self.assertEqual(bad_range.status_code, 400)

    def test_unique_import_passwords(self):
        result = self.admin.post('/api/members/import', json={
            'text': '导入A,视觉,import_person_a\n导入B,机械,import_person_b'
        })
        self.assertEqual(result.status_code, 200)
        entries = result.get_json()['created']
        self.assertEqual(len(entries), 2)
        self.assertNotEqual(entries[0].split(' / ')[-1], entries[1].split(' / ')[-1])


if __name__ == '__main__':
    unittest.main()
