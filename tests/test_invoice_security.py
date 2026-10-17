"""发票暂存归属、文件访问与元数据更新回归。"""
import io
import os
import unittest
from uuid import uuid4

from test_tasks import app_module
from modules import invoices


class TestInvoiceSecurity(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.assertTrue(self.admin.post('/login', json={
            'username': 'admin', 'password': '123456'
        }).get_json()['ok'])
        self.name = 'inv_' + uuid4().hex[:12]
        response = self.admin.post('/api/member', json={
            'name': '隔离测试成员', 'username': self.name,
            'password': 'testing-invoice-member', 'role': 'member'
        })
        self.assertTrue(response.get_json()['ok'])
        self.member = app_module.app.test_client()
        self.assertTrue(self.member.post('/login', json={
            'username': self.name, 'password': 'testing-invoice-member'
        }).get_json()['ok'])

    def _upload(self, client):
        response = client.post('/invoices/api/upload', data={
            'file': (io.BytesIO(b'%PDF-1.4\n% ' + uuid4().hex.encode('ascii') + b'\n%%EOF\n'), 'receipt.pdf')
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        self.assertTrue(data['success'], data)
        return data['tmp_path']

    def test_cross_account_upload_cannot_be_claimed_or_cleaned(self):
        own = self._upload(self.member)
        foreign = self._upload(self.admin)
        self.assertNotEqual(os.path.dirname(own), os.path.dirname(foreign))
        payload = {'tmp_path': foreign, 'month': '2026.10', 'amount': 0}
        response = self.member.post('/invoices/api/upload/confirm', json=payload)
        self.assertEqual(response.status_code, 400)
        batch = self.member.post('/invoices/api/upload/confirm/batch',
                                 json={'items': [payload]})
        self.assertEqual(batch.get_json()['error'], 1)
        cleanup = self.member.post('/invoices/api/upload/cleanup',
                                   json={'tmp_paths': [foreign]})
        self.assertEqual(cleanup.get_json()['removed'], 0)
        self.assertEqual(self.member.post('/invoices/api/upload/cleanup',
                         json={'tmp_paths': [own]}).get_json()['removed'], 1)
        self.assertEqual(self.admin.post('/invoices/api/upload/cleanup',
                         json={'tmp_paths': [foreign]}).get_json()['removed'], 1)

    def test_confirm_and_status_move_migrate_metadata_key(self):
        rel = self._upload(self.member)
        confirmed = self.member.post('/invoices/api/upload/confirm', json={
            'tmp_path': rel, 'month': '2026.10', 'status': '已报销',
            'submitter': '其他人', 'amount': 15
        })
        self.assertTrue(confirmed.get_json()['success'], confirmed.get_json())
        old = confirmed.get_json()['rel_path']
        self.assertIn(old, invoices.load_meta())
        self.assertEqual(invoices.load_meta()[old]['submitter'], '隔离测试成员')
        self.assertEqual(invoices.load_meta()[old]['status'], '未报销')
        moved = self.admin.post('/invoices/api/invoice/update', json={
            'id': old, 'status': '正在报销'
        })
        self.assertTrue(moved.get_json()['success'], moved.get_json())
        self.assertTrue(moved.get_json()['moved'])
        new = old.replace('/未报销/', '/正在报销/')
        meta = invoices.load_meta()
        self.assertNotIn(old, meta)
        self.assertIn(new, meta)
        self.assertTrue(os.path.isfile(os.path.join(invoices.BASE_DIR, new)))

    def test_cannot_treat_existing_invoice_as_temp(self):
        path_dir = os.path.join(invoices.BASE_DIR, '2026.10', '未报销')
        os.makedirs(path_dir, exist_ok=True)
        name = uuid4().hex + '.pdf'
        path = os.path.join(path_dir, name)
        with open(path, 'wb') as stream:
            stream.write(b'%PDF-1.4\n%%EOF')
        try:
            rel = '2026.10/未报销/' + name
            response = self.member.post('/invoices/api/upload/confirm', json={
                'tmp_path': rel, 'month': '2026.10', 'amount': 0
            })
            self.assertEqual(response.status_code, 400)
            self.assertTrue(os.path.isfile(path))
            response = self.member.get('/invoices/api/check-duplicate',
                                       query_string={'rel_path': rel})
            self.assertEqual(response.status_code, 403)
        finally:
            os.remove(path)

    def test_same_name_member_cannot_read_another_users_invoice(self):
        own_temp = self._upload(self.member)
        confirmed = self.member.post('/invoices/api/upload/confirm', json={
            'tmp_path': own_temp, 'month': '2026.10', 'amount': 20
        }).get_json()
        self.assertTrue(confirmed['success'])
        rel = confirmed['rel_path']
        self.assertGreater(invoices.load_meta()[rel]['submitter_id'], 0)

        other_login = 'same_name_' + uuid4().hex[:12]
        created = self.admin.post('/api/member', json={
            'name': '隔离测试成员', 'username': other_login,
            'password': 'same-name-test-password'
        })
        self.assertTrue(created.get_json()['ok'])
        other = app_module.app.test_client()
        self.assertTrue(other.post('/login', json={
            'username': other_login, 'password': 'same-name-test-password'
        }).get_json()['ok'])
        self.assertEqual(other.get('/invoices/file/' + rel).status_code, 403)
        result = other.get('/invoices/api/invoices').get_json()['data']
        self.assertFalse(any(inv['rel_path'] == rel for inv in result))
        mine = self.member.get('/invoices/api/invoices').get_json()['data']
        self.assertTrue(any(inv['rel_path'] == rel for inv in mine))

    def test_bad_pdf_and_mutation_whitelist(self):
        response = self.member.post('/invoices/api/upload', data={
            'file': (io.BytesIO(b'not a pdf'), 'fake.pdf')
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)
        bad = self.admin.post('/invoices/api/invoice/batch', json={
            'ids': ['2026.10/未报销/fake.pdf'],
            'updates': {'rel_path': '../another.pdf'}
        })
        self.assertEqual(bad.status_code, 400)


if __name__ == '__main__':
    unittest.main()
