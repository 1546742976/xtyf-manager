"""Regression tests for correcting imported invoices and opening stored PDFs."""
import io
import os
import unittest
from urllib.parse import quote
from uuid import uuid4

from test_tasks import app_module
from modules import invoices


class TestInvoiceCorrectionAndPreview(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.assertTrue(self.admin.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only'
        }).get_json()['ok'])
        login = 'invoice_reg_' + uuid4().hex[:12]
        name = '发票回归成员_' + uuid4().hex[:8]
        created = self.admin.post('/api/member', json={
            'name': name, 'username': login,
            'password': 'invoice-test-password', 'role': 'member'
        }).get_json()
        self.assertTrue(created['ok'], created)
        self.member = app_module.app.test_client()
        self.assertTrue(self.member.post('/login', json={
            'username': login, 'password': 'invoice-test-password'
        }).get_json()['ok'])

    def _confirm(self, *, month='2026.10', date='2026-10-02', amount='12.50'):
        upload = self.member.post('/invoices/api/upload', data={
            'file': (io.BytesIO(b'%PDF-1.4\n% ' + uuid4().hex.encode() + b'\n%%EOF\n'), 'original.pdf')
        }, content_type='multipart/form-data')
        self.assertEqual(upload.status_code, 200)
        path = upload.get_json()['tmp_path']
        result = self.member.post('/invoices/api/upload/confirm', json={
            'tmp_path': path, 'month': month, 'date': date, 'amount': amount,
            'skip_duplicate_check': True,
        })
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        self.assertTrue(result.get_json()['success'], result.get_json())
        return result.get_json()['rel_path']

    def _invoice(self, client, rel):
        result = client.get('/invoices/api/invoices', query_string={'id': rel})
        self.assertEqual(result.status_code, 200)
        data = result.get_json()['data']
        self.assertEqual(len(data), 1)
        return data[0]

    def test_uploaded_manual_date_is_preserved_in_list(self):
        rel = self._confirm(date='2026-09-23')
        self.assertEqual(invoices.load_meta()[rel]['date'], '2026-09-23')
        item = self._invoice(self.member, rel)
        self.assertEqual(item['date'], '2026-09-23')
        self.assertEqual(item['month'], '2026.10')
        preview = self.member.get('/invoices/api/open_file', query_string={'rel_path': rel})
        self.assertTrue(preview.get_json()['success'])
        self.assertEqual(self.member.get(preview.get_json()['url']).status_code, 200)

    def test_correcting_month_date_and_status_keeps_file_and_cache_in_sync(self):
        old = self._confirm(date='2026-09-23')
        self.assertIn(old, invoices.load_cache())
        response = self.admin.post('/invoices/api/invoice/update', json={
            'id': old, 'month': '2026.11', 'date': '2026-11-08',
            'status': '正在报销', 'notes': '核对后手工修正'
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        result = response.get_json()
        self.assertTrue(result['success'], result)
        self.assertTrue(result['moved'])
        new = result['id']
        self.assertEqual(result['rel_path'], new)
        self.assertTrue(new.startswith('2026.11/正在报销/'), repr({'new': new, 'result': result, 'old': old, 'base_dir': invoices.BASE_DIR}))
        self.assertFalse(os.path.exists(os.path.join(invoices.BASE_DIR, old)))
        self.assertTrue(os.path.isfile(os.path.join(invoices.BASE_DIR, new)))
        meta = invoices.load_meta()
        cache = invoices.load_cache()
        self.assertNotIn(old, meta)
        self.assertNotIn(old, cache)
        self.assertIn(new, meta)
        self.assertIn(new, cache)
        self.assertEqual(meta[new]['date'], '2026-11-08')
        self.assertEqual(meta[new]['month'], '2026.11')
        self.assertEqual(meta[new]['notes'], '核对后手工修正')
        item = self._invoice(self.member, new)
        self.assertEqual((item['date'], item['month'], item['status']),
                         ('2026-11-08', '2026.11', '正在报销'))
        self.assertEqual(self.member.get('/invoices/file/' + quote(new, safe='/')).status_code, 200)
        self.assertEqual(self.member.get('/invoices/file/' + quote(old, safe='/')).status_code, 404)
        self.assertEqual(self.member.post('/invoices/api/invoice/update', json={
            'id': new, 'month': '2026.12'
        }).status_code, 403)

    def test_correct_date_without_moving_and_clear_override(self):
        rel = self._confirm()
        first = self.admin.post('/invoices/api/invoice/update', json={
            'id': rel, 'date': '2024-02-29'
        }).get_json()
        self.assertTrue(first['success'])
        self.assertFalse(first['moved'])
        self.assertEqual(self._invoice(self.member, rel)['date'], '2024-02-29')
        cleared = self.admin.post('/invoices/api/invoice/update', json={
            'id': rel, 'date': ''
        }).get_json()
        self.assertTrue(cleared['success'])
        self.assertEqual(self._invoice(self.member, rel)['date'], '')
        self.assertTrue(os.path.isfile(os.path.join(invoices.BASE_DIR, rel)))

    def test_invalid_month_and_date_never_mutate_invoice(self):
        rel = self._confirm()
        original = dict(invoices.load_meta()[rel])
        for bad in ('2026.13', '2026.0', '../outside', '2026..11', 202611):
            with self.subTest(month=bad):
                result = self.admin.post('/invoices/api/invoice/update',
                                         json={'id': rel, 'month': bad, 'date': '2026-10-30'})
                self.assertEqual(result.status_code, 400)
        for bad in ('2026-02-30', '2026/10/01', '2026-2-3', None):
            with self.subTest(date=bad):
                result = self.admin.post('/invoices/api/invoice/update',
                                         json={'id': rel, 'date': bad, 'month': '2026.11'})
                self.assertEqual(result.status_code, 400)
        self.assertEqual(invoices.load_meta()[rel], original)
        self.assertTrue(os.path.isfile(os.path.join(invoices.BASE_DIR, rel)))

    def test_month_relocation_uses_collision_safe_name(self):
        rel = self._confirm()
        existing_dir = os.path.join(invoices.BASE_DIR, '2026.12', '未报销')
        os.makedirs(existing_dir, exist_ok=True)
        same_name = os.path.join(existing_dir, os.path.basename(rel))
        with open(same_name, 'wb') as file:
            file.write(b'%PDF-1.4\nexisting\n%%EOF\n')
        result = self.admin.post('/invoices/api/invoice/update', json={
            'id': rel, 'month': '2026.12'
        }).get_json()
        self.assertTrue(result['success'])
        self.assertTrue(result['moved'])
        self.assertTrue(result['id'].endswith('_1.pdf'), result['id'])
        self.assertTrue(os.path.isfile(same_name))
        self.assertTrue(os.path.isfile(os.path.join(invoices.BASE_DIR, result['id'])))

    def test_preview_handles_special_characters_with_one_url_encoding(self):
        old = self._confirm()
        new = '2026.10/未报销/发票 50% #测试.pdf'
        destination = os.path.join(invoices.BASE_DIR, new)
        os.replace(os.path.join(invoices.BASE_DIR, old), destination)
        meta = invoices.load_meta()
        meta[new] = meta.pop(old)
        meta[new]['rel_path'] = new
        meta[new]['filename'] = os.path.basename(new)
        invoices.save_meta(meta)
        result = self.member.get('/invoices/api/open_file', query_string={'rel_path': new})
        self.assertEqual(result.status_code, 200, result.get_data(as_text=True))
        url = result.get_json()['url']
        self.assertIn('%25', url)
        self.assertIn('%23', url)
        self.assertEqual(self.member.get(url).status_code, 200)
        self.assertEqual(self.member.get('/invoices/file/' + quote(new, safe='/')).status_code, 200)


if __name__ == '__main__':
    unittest.main()
