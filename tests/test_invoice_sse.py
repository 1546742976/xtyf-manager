"""Streaming responses remain valid after the ordinary request teardown."""
import sqlite3
import unittest
from uuid import uuid4
from contextlib import closing
from unittest.mock import patch
from werkzeug.test import create_environ

from test_tasks import app_module
from common import DB_PATH


class TestInvoiceSSE(unittest.TestCase):
    def setUp(self):
        self.client, self.mid = self.make_client()

    def make_client(self):
        admin = app_module.app.test_client()
        admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        username = 'sse_' + uuid4().hex[:12]
        mid = admin.post('/api/member', json={'name': username, 'username': username,
            'password': 'synthetic-password', 'role': 'manager'}).get_json()['id']
        admin.post(f'/api/admin/permissions/{mid}', json={'permissions': ['invoices.manage']})
        client = app_module.app.test_client()
        client.post('/login', json={'username': username, 'password': 'synthetic-password'})
        return client, mid

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

    def test_member_stream_budget_is_released_after_close(self):
        job = self.client.post('/invoices/api/job/start', json={'type': 'refresh'}).get_json()
        url = '/invoices/api/job/stream/' + job['job_id']
        first = self.client.get(url, buffered=False)
        second = self.client.get(url, buffered=False)
        try:
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 429)
            self.assertEqual(second.get_json()['reason'], 'sse_capacity')
            self.assertEqual(second.headers['Retry-After'], '5')
        finally:
            first.close()
            second.close()
        third = self.client.get(url, buffered=False)
        try:
            self.assertEqual(third.status_code, 200)
        finally:
            third.close()

    def test_four_stream_process_budget_leaves_other_requests_available(self):
        job = self.client.post('/invoices/api/job/start', json={'type': 'refresh'}).get_json()
        url = '/invoices/api/job/stream/' + job['job_id']
        clients = [self.client] + [self.make_client()[0] for _ in range(4)]
        streams = []
        try:
            for client in clients[:4]:
                streams.append(client.get(url, buffered=False))
                self.assertEqual(streams[-1].status_code, 200)
            rejected = clients[4].get(url)
            self.assertEqual(rejected.status_code, 429)
            self.assertEqual(clients[4].get('/invoices/api/jobs').status_code, 200)
            streams.pop().close()
            streams.append(clients[4].get(url, buffered=False))
            self.assertEqual(streams[-1].status_code, 200)
        finally:
            for stream in streams:
                stream.close()

    def test_unconsumed_response_close_releases_once(self):
        job = self.client.post('/invoices/api/job/start', json={'type': 'refresh'}).get_json()
        url = '/invoices/api/job/stream/' + job['job_id']
        cookie = self.client.get_cookie('session').value
        response = app_module.app.wsgi_app(create_environ(url, headers={'Cookie': 'session=' + cookie}), lambda *args: None)
        try:
            self.assertEqual(self.client.get(url).status_code, 429)
        finally:
            response.close()
        active = self.client.get(url, buffered=False)
        try:
            self.assertEqual(active.status_code, 200)
            response.close()
            self.assertEqual(self.client.get(url).status_code, 429)
        finally:
            active.close()

    def test_stream_heartbeats_and_expires_without_losing_job(self):
        from modules import invoices
        job = self.client.post('/invoices/api/job/start', json={'type': 'refresh'}).get_json()
        url = '/invoices/api/job/stream/' + job['job_id']
        clock = [0]
        def advance(seconds):
            clock[0] += seconds
        with patch.object(invoices.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(invoices.time, 'sleep', side_effect=advance):
            response = self.client.get(url, buffered=True)
        self.assertIn(b': keep-alive', response.data)
        self.assertLessEqual(clock[0], 30)
        response.close()
        self.assertEqual(self.client.get('/invoices/api/job/' + job['job_id']).get_json()['status'], 'queued')
        again = self.client.get(url, buffered=False)
        self.assertEqual(again.status_code, 200)
        again.close()

    def test_stream_notices_permission_revocation(self):
        job = self.client.post('/invoices/api/job/start', json={'type': 'refresh'}).get_json()
        response = self.client.get('/invoices/api/job/stream/' + job['job_id'], buffered=False)
        try:
            iterator = iter(response.response)
            self.assertIn(b'queued', next(iterator))
            with closing(sqlite3.connect(DB_PATH)) as db, db:
                db.execute("DELETE FROM member_permissions WHERE member_id=? AND permission='invoices.manage'", (self.mid,))
            self.assertIn('授权已失效', next(iterator).decode())
            with self.assertRaises(StopIteration):
                next(iterator)
        finally:
            response.close()
            with closing(sqlite3.connect(DB_PATH)) as db, db:
                db.execute("INSERT OR IGNORE INTO member_permissions(member_id,permission,granted_at) "
                           "VALUES(?,'invoices.manage','2026-10-22 00:00:00')", (self.mid,))


if __name__ == '__main__':
    unittest.main()
