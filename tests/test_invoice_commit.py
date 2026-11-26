"""Upload confirmation contracts at HTTP and filesystem failure boundaries."""
import io
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_tasks import app_module
from modules import invoices


class TestInvoiceCommit(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.assertTrue(self.client.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only'}).get_json()['ok'])

    def upload(self):
        return self.client.post('/invoices/api/upload', data={
            'file': (io.BytesIO(b'%PDF-1.4\n% ' + uuid4().hex.encode() + b'\n%%EOF'), 'invoice.pdf')
        }).get_json()['tmp_path']

    def confirm(self, path, amount='1.00'):
        return self.client.post('/invoices/api/upload/confirm', json={
            'tmp_path': path, 'month': '2026.10', 'amount': amount, 'submitter_id': 1})

    def test_amount_rounded_to_zero_is_rejected_and_can_be_corrected(self):
        path = self.upload()
        response = self.confirm(path, '0.004')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['code'], 'AMOUNT_REQUIRED')
        corrected = self.confirm(path, '0.005')
        self.assertTrue(corrected.get_json()['success'])
        self.assertEqual(corrected.get_json()['amount'], '0.01')

    def test_metadata_write_failure_keeps_upload_retryable(self):
        path = self.upload()
        replace = os.replace

        def fail_metadata(source, destination):
            if os.fspath(destination) == invoices.META_FILE:
                raise OSError('injected metadata disk failure')
            return replace(source, destination)

        with patch('os.replace', side_effect=fail_metadata):
            self.assertGreaterEqual(self.confirm(path).status_code, 400)
        self.assertTrue(self.confirm(path).get_json()['success'])

    def test_cache_write_failure_does_not_report_committed_invoice_as_failed(self):
        path = self.upload()
        replace = os.replace

        def fail_cache(source, destination):
            if os.fspath(destination) == invoices.CACHE_FILE:
                raise OSError('injected cache disk failure')
            return replace(source, destination)

        with patch('os.replace', side_effect=fail_cache):
            response = self.confirm(path)
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result['success'])
        preview = self.client.get('/invoices/file/' + result['rel_path'])
        try:
            self.assertEqual(preview.status_code, 200)
        finally:
            preview.close()

    def test_atomic_move_failure_keeps_upload_retryable(self):
        path = self.upload()
        with patch('os.rename', side_effect=OSError('cross-device move forbidden')):
            self.assertEqual(self.confirm(path).status_code, 503)
        self.assertTrue(self.confirm(path).get_json()['success'])

    def test_missing_zero_and_negative_amounts_leave_upload_retryable(self):
        path = self.upload()
        for amount in ('', '0', '-1'):
            with self.subTest(amount=amount):
                self.assertEqual(self.confirm(path, amount).status_code, 400)
        missing = self.client.post('/invoices/api/upload/confirm', json={
            'tmp_path': path, 'month': '2026.10', 'submitter_id': 1})
        self.assertEqual(missing.status_code, 400)
        self.assertTrue(self.confirm(path).get_json()['success'])

    def test_failed_metadata_compensation_reports_manual_reconciliation_and_keeps_upload(self):
        path = self.upload()
        replace = os.replace
        metadata_writes = 0

        def fail_rollback(source, destination):
            nonlocal metadata_writes
            if os.fspath(destination) == invoices.META_FILE:
                metadata_writes += 1
                if metadata_writes == 2:
                    raise OSError('rollback disk failure')
            return replace(source, destination)

        with patch('os.rename', side_effect=OSError('rename failure')), patch('os.replace', side_effect=fail_rollback):
            response = self.confirm(path)
        self.assertEqual(response.status_code, 503)
        self.assertIn('元数据恢复失败', response.get_json()['error'])
        self.assertEqual(self.client.post('/invoices/api/upload/cleanup',
                                         json={'tmp_paths': [path]}).get_json()['removed'], 1)

    def test_batch_keeps_zero_amount_upload_for_retry_and_accepts_positive_item(self):
        first, second = self.upload(), self.upload()
        result = self.client.post('/invoices/api/upload/confirm/batch', json={'items': [
            {'tmp_path': first, 'month': '2026.10', 'amount': '0', 'submitter_id': 1},
            {'tmp_path': second, 'month': '2026.10', 'amount': '2', 'submitter_id': 1},
        ]}).get_json()
        self.assertEqual((result['ok'], result['error']), (1, 1))
        self.assertEqual(result['results'][0]['code'], 'AMOUNT_REQUIRED')
        self.assertEqual(result['results'][0]['tmp_path'], first)
        self.assertTrue(self.confirm(first).get_json()['success'])


if __name__ == '__main__':
    unittest.main()
