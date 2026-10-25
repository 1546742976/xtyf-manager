"""Boundary regressions for the October backend review (synthetic data only)."""
import io
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_tasks import app_module
from modules import collaboration_storage


class BackendLimitsTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.assertTrue(self.client.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'}).get_json()['ok'])

    def test_long_task_title_and_numeric_progress_return_400(self):
        self.assertEqual(self.client.post('/api/task', json={'title': '长'*201}).status_code, 400)
        response = self.client.post('/api/task/1/progress', data={'progress': '9'*4400, 'version': '1'})
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/task/1/progress', data={'progress':'1','version':'9'*4400})
        self.assertEqual(response.status_code, 400)
        for field in ('progress', 'version', 'project_version'):
            with self.subTest(field=field):
                data = {'progress':'1','version':'1','project_version':'1', field:'9'*4400}
                self.assertEqual(self.client.post('/api/daily-projects/1/groups/mechanical/progress', data=data).status_code, 400)

    def test_import_partial_success_rejects_duplicates_and_too_many_rows(self):
        username = 'import_'+uuid4().hex[:12]
        result = self.client.post('/api/members/import', json={'text': f'测试,,{username}\n重复,,{username}\n,组别,坏行'}).get_json()
        self.assertEqual(len(result['created']), 1)
        self.assertEqual([x['line'] for x in result['failures']], [2, 3])
        self.assertEqual(self.client.post('/api/members/import', json={'text': '\n'.join(['甲']*501)}).status_code, 400)

    def test_import_control_separators_cannot_create_extra_members(self):
        text = 'control_' + uuid4().hex[:10] + '\x1e' + 'extra_' + uuid4().hex[:10]
        result = self.client.post('/api/members/import', json={'text':text}).get_json()
        self.assertEqual(result['created'], [])
        self.assertEqual(result['failures'][0]['line'], 1)

    def test_nested_api_errors_are_json(self):
        for path in ('/funds/api/not-found', '/invoices/api/not-found', '/discussions/api/not-found'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertTrue(response.is_json)

    def test_ordinary_body_limit_and_asset_specific_limit(self):
        response = self.client.post('/api/task', data=b' '* (17*1024*1024), content_type='application/json')
        self.assertEqual(response.status_code, 413)
        self.assertTrue(response.is_json)
        self.assertEqual(self.client.post('/api/assets/not-found', data=b'x'*17000).status_code, 413)

    def test_invoice_upload_can_exceed_global_body_limit(self):
        # Actual multipart parsing and disk storage; OCR/duplicate lookup are unrelated.
        from modules import invoices
        content = b'%PDF-1.4\n' + b' ' * (17 * 1024 * 1024)
        info = {'amount': '1.00', 'date':'2026-10-22', 'vendor':'模拟', 'category':''}
        with patch.object(invoices, 'extract_invoice_info', return_value=info), patch.object(invoices, 'check_duplicates', return_value=[]):
            response = self.client.post('/invoices/api/upload', data={'file':(io.BytesIO(content),'limit-test.pdf')})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()['success'])
        response.close()

    def test_page_errors_and_vendor_contract(self):
        for path in ('/missing-page', '/vendor/bootstrap/../../app.py'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertIn('页面或资源不存在', response.get_data(as_text=True))
        self.assertEqual(self.client.get('/favicon.ico').status_code, 302)
        response = self.client.get('/vendor/bootstrap/bootstrap.min.css')
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/css', response.content_type)
            self.assertIn('max-age=604800', response.headers['Cache-Control'])
        finally:
            response.close()

    def test_funds_invalid_date_and_text_are_400(self):
        self.assertEqual(self.client.get('/funds/api/records?date_from=bad').status_code, 400)
        response = self.client.post('/funds/api/records', json={'request_id': uuid4().hex, 'record_type':'进','amount':'1','record_date':'2026-10-22','notes':'长'*2001})
        self.assertEqual(response.status_code, 400)

    def test_quota_is_enforced_after_idempotency(self):
        username = 'quota_' + uuid4().hex[:12]
        self.client.post('/api/member', json={'name':'配额成员','username':username,'password':'quota-password'})
        self.client.post('/login', json={'username':username,'password':'quota-password'})
        sid = str(uuid4())
        def send():
            return self.client.post('/discussions/api/topics', data={'submission_id':sid,'title':'配额测试','body':'','files':(io.BytesIO(b'1234'),'data.txt')})
        with patch.object(collaboration_storage, 'MEMBER_QUOTA_BYTES', 5, create=True):
            first = send()
            self.assertEqual(first.status_code, 201, first.get_json())
            self.assertEqual(send().status_code, 200)
            response = self.client.post('/discussions/api/topics', data={'submission_id':str(uuid4()),'title':'超配额','body':'','files':(io.BytesIO(b'1234'),'data.txt')})
            self.assertEqual(response.status_code, 413)

    def test_concurrent_uploads_cannot_exceed_member_quota(self):
        username = 'quota_parallel_' + uuid4().hex[:12]
        self.client.post('/api/member', json={'name':'并发成员','username':username,'password':'quota-password'})
        clients = [app_module.app.test_client() for _ in range(2)]
        for client in clients:
            client.post('/login', json={'username':username,'password':'quota-password'})
        barrier = threading.Barrier(2)
        original = collaboration_storage.stage_uploads
        def stage(*args):
            result = original(*args)
            barrier.wait(timeout=10)
            return result
        def send(client):
            return client.post('/discussions/api/topics', data={'submission_id':str(uuid4()),'title':'并发配额','body':'','files':(io.BytesIO(b'1234'),'data.txt')}).status_code
        with patch.object(collaboration_storage, 'MEMBER_QUOTA_BYTES', 5), patch('modules.discussions.stage_uploads', side_effect=stage):
            with ThreadPoolExecutor(max_workers=2) as executor:
                statuses = list(executor.map(send, clients))
        self.assertEqual(sorted(statuses), [201, 413])


if __name__ == '__main__':
    unittest.main()
