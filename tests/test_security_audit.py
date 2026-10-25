"""Audit retention and abuse boundaries, using only synthetic test databases."""
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

from flask.testing import FlaskClient
from test_tasks import app_module
from modules import security


class AuditTests(unittest.TestCase):
    def rows(self):
        with closing(sqlite3.connect(app_module.DB_PATH)) as db:
            return db.execute('SELECT id,action,outcome FROM security_audit ORDER BY id').fetchall()

    def test_anonymous_non_login_does_not_grow_audit(self):
        client = app_module.app.test_client()
        before = len(self.rows())
        client.post('/api/task', json={'title': '匿名'})
        self.assertEqual(len(self.rows()), before)

    def test_authenticated_csrf_failure_is_audited(self):
        client = app_module.app.test_client()
        client.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        response = FlaskClient.open(client, '/api/task', method='POST', json={'title': '拒绝'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.rows()[-1][2], 'failure')

    def test_authenticated_oversized_requests_keep_minimal_audit(self):
        client = app_module.app.test_client()
        client.post('/login', json={'username':'admin','password':'ci-test-password-only'})
        for path, size, overrides in (
            ('/api/task', 17*1024*1024, {}),
            ('/api/assets/not-found', 17000, {'CONTENT_LENGTH':None, 'wsgi.input_terminated':True}),
        ):
            with self.subTest(path=path):
                before = len(self.rows())
                response = client.post(path, data=b'x'*size, content_type='application/json', environ_overrides=overrides)
                self.assertEqual(response.status_code, 413)
                self.assertEqual(len(self.rows()), before + 1)
                with closing(sqlite3.connect(app_module.DB_PATH)) as db:
                    row = db.execute('SELECT actor_id,outcome,http_status,details FROM security_audit ORDER BY id DESC LIMIT 1').fetchone()
                self.assertIsNotNone(row[0])
                self.assertEqual(row[1:], ('failure', 413, '{}'))

    def test_successful_login_survives_failure_throttling(self):
        client = app_module.app.test_client()
        client.post('/login', json={'username': 'audit_unknown', 'password': 'bad'})
        client.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        self.assertEqual(self.rows()[-1][1:], ('login', 'success'))

    def test_retention_protects_pending_and_job_request_chain(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('CREATE TABLE security_audit(id INTEGER PRIMARY KEY,details TEXT,outcome TEXT,created_at TEXT); CREATE TABLE invoice_jobs(audit_id INTEGER);')
            for identifier in range(1, 9):
                db.execute('INSERT INTO security_audit VALUES(?,?,?,?)', (identifier, '{}', 'success', '2000-01-01 00:00:00' if identifier < 5 else '2999-01-01 00:00:00'))
            db.execute("UPDATE security_audit SET outcome='pending' WHERE id=1")
            db.execute('UPDATE security_audit SET details=? WHERE id=2', (json.dumps({'request_audit_id': 3}),))
            db.execute('INSERT INTO invoice_jobs VALUES(2)')
            db.commit()
            db.execute("ALTER TABLE security_audit ADD COLUMN level TEXT NOT NULL DEFAULT 'critical'")
            with patch.dict('os.environ', {'AUDIT_RETENTION_DAYS': '90', 'AUDIT_MAX_ROWS': '4'}):
                plan = security.cleanup_audit_rows(db)
                self.assertEqual(plan['ids'], [4])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM security_audit').fetchone()[0], 8)
                security.cleanup_audit_rows(db, confirmed=True)
            self.assertEqual([row[0] for row in db.execute('SELECT id FROM security_audit ORDER BY id')], [1, 2, 3, 5, 6, 7, 8])


if __name__ == '__main__':
    unittest.main()
