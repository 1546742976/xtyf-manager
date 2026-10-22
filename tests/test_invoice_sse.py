"""Streaming responses remain valid after the ordinary request teardown."""
import sqlite3
import unittest
from contextlib import closing

from test_tasks import app_module
from common import DB_PATH


class TestInvoiceSSE(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.assertTrue(self.client.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only'}).get_json()['ok'])

    def test_terminal_stream_can_be_consumed_after_request_teardown(self):
        job = self.client.post('/invoices/api/job/start', json={'type': 'refresh'}).get_json()
        with closing(sqlite3.connect(DB_PATH)) as db, db:
            db.execute("UPDATE invoice_jobs SET status='done',result='{}' WHERE id=?", (job['job_id'],))
        response = self.client.get('/invoices/api/job/stream/' + job['job_id'], buffered=False)
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn('"status": "done"', b''.join(response.response).decode())
        finally:
            response.close()

    def test_stream_notices_permission_revocation(self):
        job = self.client.post('/invoices/api/job/start', json={'type': 'refresh'}).get_json()
        response = self.client.get('/invoices/api/job/stream/' + job['job_id'], buffered=False)
        try:
            iterator = iter(response.response)
            self.assertIn(b'queued', next(iterator))
            with closing(sqlite3.connect(DB_PATH)) as db, db:
                db.execute("DELETE FROM member_permissions WHERE member_id=1 AND permission='invoices.manage'")
            self.assertIn('授权已失效', next(iterator).decode())
            with self.assertRaises(StopIteration):
                next(iterator)
        finally:
            response.close()
            with closing(sqlite3.connect(DB_PATH)) as db, db:
                db.execute("INSERT OR IGNORE INTO member_permissions(member_id,permission,granted_at) "
                           "VALUES(1,'invoices.manage','2026-10-22 00:00:00')")


if __name__ == '__main__':
    unittest.main()
