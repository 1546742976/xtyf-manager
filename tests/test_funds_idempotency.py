"""Funds creation receipts survive retries, deletion and projection failures."""
import unittest
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from test_tasks import app_module
from modules import funds, funds_requests


class FundsIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.assertTrue(self.client.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only',
        }).get_json()['ok'])
        self.body = {'request_id': uuid4().hex, 'record_type': '进',
                     'amount': '100.00', 'record_date': '2026-10-24',
                     'source_dest': '合成资金测试', 'notes': uuid4().hex}

    def test_retry_creates_only_one_record(self):
        first = self.client.post('/funds/api/records', json=self.body).get_json()
        second = self.client.post('/funds/api/records', json=self.body).get_json()
        self.assertEqual(first['id'], second['id'])
        records = self.client.get('/funds/api/records').get_json()['data']
        self.assertEqual(len([r for r in records if r['notes'] == self.body['notes']]), 1)

    def test_required_key_and_capability(self):
        capability = self.client.get('/funds/api/capabilities').get_json()
        self.assertEqual(capability['create_idempotency'], 1)
        self.assertGreater(capability['member_id'], 0)
        for key in (None, '', [], True, 'short', 'x'*101):
            body = {**self.body, 'request_id': key}
            if key is None:
                del body['request_id']
            response = self.client.post('/funds/api/records', json=body)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()['reason'], 'idempotency_required')

    def test_conflict_and_distinct_intents(self):
        first = self.client.post('/funds/api/records', json=self.body).get_json()
        conflict = self.client.post('/funds/api/records', json={**self.body, 'amount': '101'})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()['reason'], 'request_id_conflict')
        second = self.client.post('/funds/api/records', json={**self.body, 'request_id': uuid4().hex}).get_json()
        self.assertNotEqual(first['id'], second['id'])

    def test_deleted_record_retains_receipt(self):
        first = self.client.post('/funds/api/records', json=self.body).get_json()
        self.assertEqual(self.client.delete(f"/funds/api/records/{first['id']}?version=1").status_code, 200)
        replay = self.client.post('/funds/api/records', json=self.body).get_json()
        self.assertEqual(replay['id'], first['id'])
        self.assertTrue(replay['replayed'])
        self.assertEqual(self.client.get(f"/funds/api/records/{first['id']}").status_code, 404)

    def test_file_failure_does_not_hide_commit_and_retry_repairs_projection(self):
        original = Path(funds.FUNDS_TXT).read_bytes()
        with patch('modules.funds.os.replace', side_effect=PermissionError('synthetic ledger fault')):
            response = self.client.post('/funds/api/records', json=self.body)
        self.assertEqual(response.status_code, 200)
        first = response.get_json()
        self.assertEqual(first['ledger_sync'], 'failed')
        self.assertEqual(Path(funds.FUNDS_TXT).read_bytes(), original)
        self.assertEqual(self.client.get(f"/funds/api/records/{first['id']}").status_code, 200)
        replay = self.client.post('/funds/api/records', json=self.body).get_json()
        self.assertEqual(replay['id'], first['id'])
        self.assertEqual(replay['ledger_sync'], 'ok')
        self.assertIn(self.body['notes'], Path(funds.FUNDS_TXT).read_text(encoding='utf-8-sig'))

    def test_receipt_failure_rolls_back_record(self):
        conn = funds.get_funds_db()
        conn.execute("CREATE TRIGGER audit_receipt_fault BEFORE INSERT ON funds_requests BEGIN SELECT RAISE(ABORT,'synthetic receipt fault'); END")
        conn.commit()
        try:
            self.assertEqual(self.client.post('/funds/api/records', json=self.body).status_code, 500)
        finally:
            conn.execute('DROP TRIGGER audit_receipt_fault')
            conn.commit()
            conn.close()
        records = self.client.get('/funds/api/records').get_json()['data']
        self.assertFalse(any(r['notes'] == self.body['notes'] for r in records))
        self.assertEqual(self.client.post('/funds/api/records', json=self.body).status_code, 200)

    def test_edit_review_and_delete_report_committed_projection_failure(self):
        record = self.client.post('/funds/api/records', json=self.body).get_json()
        url = f"/funds/api/records/{record['id']}"
        with patch('modules.funds.os.replace', side_effect=PermissionError('synthetic')):
            changed = self.client.put(url, json={'version': 1, 'amount': '110.00'}).get_json()
            self.assertEqual(changed['ledger_sync'], 'failed')
            reviewed = self.client.post(url + '/review', json={'version': changed['version']}).get_json()
            self.assertEqual(reviewed['ledger_sync'], 'failed')
            removed = self.client.delete(url + '?version=' + str(reviewed['version'])).get_json()
            self.assertEqual(removed['ledger_sync'], 'failed')
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_manual_ledger_repair_keeps_committed_record_and_persists_state(self):
        from modules import funds
        with patch('modules.funds.os.replace', side_effect=PermissionError('synthetic')):
            result = self.client.post('/funds/api/records', json=self.body).get_json()
        self.assertEqual(result['ledger_sync'], 'failed')
        state = self.client.get('/funds/api/ledger-sync')
        self.assertEqual(state.status_code, 200)
        self.assertNotEqual(state.get_json()['state'], 'ok')
        before = self.client.get('/funds/api/records').get_json()['data']
        repaired = self.client.post('/funds/api/ledger-sync', json={})
        self.assertEqual(repaired.status_code, 200)
        self.assertEqual(repaired.get_json()['state'], 'ok')
        self.assertEqual(self.client.get('/funds/api/records').get_json()['data'], before)
        with open(funds.FUNDS_TXT, encoding='utf-8-sig') as file:
            self.assertIn(self.body['notes'], file.read())
        with patch('modules.funds.can_review', return_value=False):
            self.assertEqual(self.client.post('/funds/api/ledger-sync', json={}).status_code, 403)

    def test_concurrent_same_request(self):
        clients = [app_module.app.test_client() for _ in range(4)]
        for client in clients:
            self.assertTrue(client.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'}).get_json()['ok'])
        with ThreadPoolExecutor(max_workers=4) as executor:
            replies = list(executor.map(lambda c: c.post('/funds/api/records', json=self.body).get_json(), clients))
        self.assertEqual(len({r['id'] for r in replies}), 1)
        self.assertEqual(sum(not r['replayed'] for r in replies), 1)

    def test_revision_changed_during_projection_remains_pending(self):
        from modules import funds
        record = self.client.post('/funds/api/records', json=self.body).get_json()
        replace = funds.os.replace
        def concurrent_write(source, target):
            conn = funds.get_funds_db()
            try:
                conn.execute('UPDATE records SET notes=? WHERE id=?', ('concurrent synthetic revision', record['id']))
                conn.commit()
            finally:
                conn.close()
            return replace(source, target)
        with patch('modules.funds.os.replace', side_effect=concurrent_write):
            result = self.client.post('/funds/api/ledger-sync', json={}).get_json()
        self.assertEqual(result['state'], 'pending')
        self.assertGreater(result['revision'], result['synced_revision'])
        with patch('modules.funds.os.replace', side_effect=PermissionError('synthetic repair fault')):
            self.assertEqual(self.client.post('/funds/api/ledger-sync', json={}).status_code, 503)
        self.assertEqual(self.client.get('/funds/api/ledger-sync').get_json()['state'], 'failed')
        self.assertEqual(self.client.post('/funds/api/ledger-sync', json={}).get_json()['state'], 'ok')

    def test_account_isolation_and_account_switch_after_preflight(self):
        username = 'funds_' + uuid4().hex[:12]
        self.client.post('/api/member', json={'name': username, 'username': username,
                         'password': 'synthetic-funds-password', 'role': 'manager'})
        another = app_module.app.test_client()
        self.assertTrue(another.post('/login', json={'username': username, 'password': 'synthetic-funds-password'}).get_json()['ok'])
        actor = self.client.get('/funds/api/capabilities').get_json()['member_id']
        self.assertEqual(another.post('/funds/api/records', json={**self.body, 'expected_member_id': actor}).status_code, 403)
        first = self.client.post('/funds/api/records', json=self.body).get_json()
        second = another.post('/funds/api/records', json=self.body).get_json()
        self.assertNotEqual(first['id'], second['id'])

    def test_repeated_migration_and_backup_preserve_receipts(self):
        first = self.client.post('/funds/api/records', json=self.body).get_json()
        conn = funds.get_funds_db()
        before = list(conn.iterdump())
        funds_requests.migrate(conn)
        funds_requests.migrate(conn)
        self.assertEqual(before, list(conn.iterdump()))
        with tempfile.TemporaryDirectory() as directory:
            restored = sqlite3.connect(str(Path(directory) / 'restored.db'))
            try:
                conn.backup(restored)
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM funds_requests')],
                                 list(restored.execute('SELECT * FROM funds_requests')))
            finally:
                restored.close()
                conn.close()
        self.assertEqual(self.client.post('/funds/api/records', json=self.body).get_json()['id'], first['id'])
