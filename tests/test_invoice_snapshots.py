"""Readers see only completed invoice scans while a writer is active."""
import io
import os
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_tasks import app_module
from modules import invoices


class TestInvoiceSnapshots(unittest.TestCase):
    def setUp(self):
        self.client = self.login()

    def login(self):
        client = app_module.app.test_client()
        self.assertTrue(client.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only'}).get_json()['ok'])
        return client

    def upload(self, amount='8.25'):
        path = self.client.post('/invoices/api/upload', data={
            'file': (io.BytesIO(b'%PDF-1.4\n% ' + uuid4().hex.encode() + b'\n%%EOF'), 'snapshot.pdf')
        }).get_json()['tmp_path']
        return path, {'tmp_path': path, 'month': '2026.10', 'amount': amount, 'submitter_id': 1}

    def test_rejected_writes_do_not_scan_or_change_snapshot_state(self):
        _, item = self.upload('0')
        with open(invoices.SNAPSHOT_STATE_FILE, 'rb') as file:
            before = file.read()
        with patch.object(invoices, 'scan_invoices', wraps=invoices.scan_invoices) as scan:
            self.assertEqual(self.client.post('/invoices/api/upload/confirm', json=item).status_code, 400)
            self.assertEqual(self.client.post('/invoices/api/upload/confirm', json={**item, 'amount': '3', 'month': 'bad-month'}).status_code, 400)
            with patch.object(invoices, '_is_finance', return_value=False):
                self.assertEqual(self.client.post('/invoices/api/invoice/update', json={'id': 'missing.pdf'}).status_code, 403)
            scan.assert_not_called()
        with open(invoices.SNAPSHOT_STATE_FILE, 'rb') as file:
            self.assertEqual(file.read(), before)

    def test_partially_successful_batch_publishes_once(self):
        _, valid = self.upload()
        _, invalid = self.upload('0')
        with patch.object(invoices, 'scan_invoices', wraps=invoices.scan_invoices) as scan:
            response = self.client.post('/invoices/api/upload/confirm/batch', json={'items': [valid, invalid]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(scan.call_count, 1)

    def test_partial_commit_followed_by_500_publishes_but_remains_failed(self):
        _, item = self.upload()
        commit = invoices._commit_upload
        def committed_then_failed(*args, **kwargs):
            commit(*args, **kwargs)
            raise RuntimeError('synthetic failure after durable commit')
        with patch.object(invoices, '_commit_upload', side_effect=committed_then_failed), patch.object(invoices, 'scan_invoices', wraps=invoices.scan_invoices) as scan:
            response = self.client.post('/invoices/api/upload/confirm', json=item)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(self.client.get('/invoices/api/invoices').headers['X-Invoice-Snapshot-State'], 'failed')
        self.client.post('/invoices/api/refresh')

    def test_failed_atomic_move_restores_metadata_without_scanning(self):
        _, item = self.upload()
        before = invoices.load_meta()
        with patch('modules.invoices.os.rename', side_effect=OSError('synthetic disk fault')), patch.object(invoices, 'scan_invoices', wraps=invoices.scan_invoices) as scan:
            response = self.client.post('/invoices/api/upload/confirm', json=item)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(scan.call_count, 0)
        self.assertEqual(invoices.load_meta(), before)

    def test_long_confirmation_returns_previous_complete_snapshot_then_new_snapshot(self):
        _, initial = self.upload()
        self.assertTrue(self.client.post('/invoices/api/upload/confirm', json=initial).get_json()['success'])
        before = self.client.get('/invoices/api/invoices').get_json()
        _, pending = self.upload('7.50')
        writer = self.login()
        entered, resume = threading.Event(), threading.Event()
        rename = os.rename
        outcome = []

        def slow_rename(source, destination):
            if os.fspath(source).endswith(os.path.basename(pending['tmp_path'])):
                entered.set()
                if not resume.wait(8):
                    raise RuntimeError('test writer not resumed')
            return rename(source, destination)

        def write():
            outcome.append(writer.post('/invoices/api/upload/confirm', json=pending))

        thread = threading.Thread(target=write)
        with patch('os.rename', side_effect=slow_rename):
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                response = self.client.get('/invoices/api/invoices')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get('X-Invoice-Snapshot-State'), 'updating')
                self.assertEqual(response.get_json(), before)
                for path in ('/invoices/api/stats', '/invoices/api/submitters', '/invoices/api/export/download'):
                    reader = self.client.get(path)
                    self.assertEqual(reader.status_code, 200, path)
                    self.assertEqual(reader.headers.get('X-Invoice-Snapshot-State'), 'updating', path)
                    reader.close()
            finally:
                resume.set()
                thread.join(10)
        self.assertTrue(outcome[0].get_json()['success'])
        after = self.client.get('/invoices/api/invoices')
        self.assertEqual(after.headers.get('X-Invoice-Snapshot-State'), 'current')
        self.assertEqual(after.get_json()['total'], before['total'] + 1)

    def test_failed_publication_preserves_complete_rows_and_reports_failure(self):
        before = self.client.get('/invoices/api/invoices').get_json()
        _, pending = self.upload()
        replace = os.replace

        def fail_snapshot(source, destination):
            if os.fspath(destination) == invoices.SNAPSHOT_FILE:
                raise OSError('snapshot disk failure')
            return replace(source, destination)

        with patch('os.replace', side_effect=fail_snapshot):
            committed = self.client.post('/invoices/api/upload/confirm', json=pending)
        self.assertTrue(committed.get_json()['success'])
        response = self.client.get('/invoices/api/invoices')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['X-Invoice-Snapshot-State'], 'failed')
        self.assertEqual(response.get_json(), before)
        self.assertEqual(self.client.post('/invoices/api/refresh').status_code, 200)
        recovered = self.client.get('/invoices/api/invoices')
        self.assertEqual(recovered.headers['X-Invoice-Snapshot-State'], 'current')
        self.assertEqual(recovered.get_json()['total'], before['total'] + 1)

    def test_no_complete_snapshot_returns_initializing_503_without_scanning(self):
        paths = (invoices.SNAPSHOT_FILE, invoices.SNAPSHOT_STATE_FILE)
        for path in paths:
            os.rename(path, path + '.test-backup')
        try:
            response = self.client.get('/invoices/api/invoices')
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.get_json()['code'], 'INVOICE_SNAPSHOT_INITIALIZING')
            self.assertEqual(response.headers['Retry-After'], '2')
            with open(invoices.SNAPSHOT_STATE_FILE, 'w', encoding='utf-8') as stream:
                stream.write('{"state":"failed"}')
            failed = self.client.get('/invoices/api/invoices')
            self.assertEqual(failed.status_code, 503)
            self.assertEqual(failed.get_json()['code'], 'INVOICE_SNAPSHOT_FAILED')
        finally:
            for path in paths:
                if os.path.exists(path):
                    os.remove(path)
                os.rename(path + '.test-backup', path)

    def test_snapshot_reads_do_not_open_pdfs_or_mutate_invoice_files(self):
        import builtins
        from pathlib import Path
        _, item = self.upload()
        self.assertTrue(self.client.post('/invoices/api/upload/confirm', json=item).get_json()['success'])
        watched = [Path(path) for path in (invoices.META_FILE, invoices.CACHE_FILE,
                                         invoices.SNAPSHOT_FILE, invoices.SNAPSHOT_STATE_FILE)]
        previous = {path: path.read_bytes() for path in watched}
        real_open = builtins.open

        def no_pdf(path, *args, **kwargs):
            if isinstance(path, (str, os.PathLike)) and os.fspath(path).lower().endswith('.pdf'):
                raise AssertionError('snapshot reader attempted PDF I/O')
            return real_open(path, *args, **kwargs)

        with patch('builtins.open', side_effect=no_pdf):
            for path in ('/invoices/api/invoices', '/invoices/api/stats', '/invoices/api/submitters',
                         '/invoices/api/export/download'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, path)
                response.close()
        self.assertEqual({path: path.read_bytes() for path in watched}, previous)

    def test_stale_snapshot_rechecks_current_ownership(self):
        viewers = []
        for _ in range(2):
            username = 'snapshot-owner-' + uuid4().hex[:10]
            created = self.client.post('/api/member', json={
                'name': username, 'username': username, 'password': 'snapshot-owner-test',
                'role': 'member'}).get_json()
            member = app_module.app.test_client()
            login = member.post('/login', json={'username': username, 'password': 'snapshot-owner-test'}).get_json()
            self.assertTrue(login['ok'])
            viewers.append((created['id'], member))
        _, item = self.upload()
        item['submitter_id'] = viewers[0][0]
        confirmed = self.client.post('/invoices/api/upload/confirm', json=item).get_json()
        rel = confirmed['rel_path']
        self.assertEqual(viewers[0][1].get('/invoices/api/invoices', query_string={'id': rel}).get_json()['total'], 1)
        replace = os.replace

        def fail_snapshot(source, destination):
            if os.fspath(destination) == invoices.SNAPSHOT_FILE:
                raise OSError('snapshot disk failure')
            return replace(source, destination)

        with patch('os.replace', side_effect=fail_snapshot):
            response = self.client.post('/invoices/api/invoice/update', json={'id': rel, 'submitter_id': viewers[1][0]})
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(viewers[0][1].get('/invoices/api/invoices', query_string={'id': rel}).get_json()['total'], 0)
        newly_owned = viewers[1][1].get('/invoices/api/invoices', query_string={'id': rel})
        self.assertEqual(newly_owned.headers['X-Invoice-Snapshot-State'], 'failed')
        self.assertEqual(newly_owned.get_json()['total'], 1)
        self.client.post('/invoices/api/refresh')
