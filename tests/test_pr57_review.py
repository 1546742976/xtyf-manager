import sqlite3
import unittest
from contextlib import closing
from uuid import uuid4
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from test_tasks import app_module


class ReviewSecurityTests(unittest.TestCase):
    def setUp(self):
        with closing(sqlite3.connect(app_module.DB_PATH)) as db, db:
            db.execute('DELETE FROM login_attempts')
        self.client = app_module.app.test_client()
        self.assertTrue(self.client.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'}).get_json()['ok'])

    def test_import_rejects_whole_batch_before_creating_accounts(self):
        username = 'review_' + uuid4().hex
        response = self.client.post('/api/members/import', json={'text': f'正常,,{username}\n重复,,admin'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['failures'][0]['line'], 2)
        with closing(sqlite3.connect(app_module.DB_PATH)) as db:
            self.assertIsNone(db.execute('SELECT id FROM members WHERE username=?', (username,)).fetchone())

    def test_same_batch_duplicate_is_atomic(self):
        username = 'review_' + uuid4().hex
        response = self.client.post('/api/members/import', json={'text': f'甲,,{username}\n乙,,{username}'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['created'], [])

    def test_preferences_do_not_add_business_audit(self):
        with closing(sqlite3.connect(app_module.DB_PATH)) as db:
            before = db.execute('SELECT COUNT(*) FROM security_audit').fetchone()[0]
        self.assertEqual(self.client.post('/api/account/preferences', json={'theme': 'dark'}).status_code, 200)
        with closing(sqlite3.connect(app_module.DB_PATH)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM security_audit').fetchone()[0], before)

    def test_failed_accounts_do_not_lock_shared_proxy(self):
        anonymous = app_module.app.test_client()
        with closing(sqlite3.connect(app_module.DB_PATH)) as db, db:
            db.execute('DELETE FROM login_attempts')
        for i in range(41):
            anonymous.post('/login', json={'username': f'absent_review_{i}', 'password': 'bad-password'})
        self.assertEqual(anonymous.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'}).status_code, 200)

    def test_concurrent_import_has_one_winner(self):
        username = 'review_' + uuid4().hex
        clients = [app_module.app.test_client(), app_module.app.test_client()]
        for client in clients:
            client.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda client: client.post('/api/members/import', json={'text': f'甲,,{username}'}).status_code, clients))
        self.assertEqual(sorted(responses), [200, 400])

    def test_insertion_failure_rolls_back_entire_batch(self):
        from modules import task_catalogs
        username = 'review_' + uuid4().hex
        original = task_catalogs._set_member_projects
        calls = []
        def conflict(*args):
            calls.append(1)
            if len(calls) == 2:
                raise sqlite3.IntegrityError('synthetic conflict')
            return original(*args)
        with patch.object(task_catalogs, '_set_member_projects', side_effect=conflict):
            response = self.client.post('/api/members/import', json={'text': f'甲,,{username}\n乙,,{username}_b'})
        self.assertEqual(response.status_code, 409)
        with closing(sqlite3.connect(app_module.DB_PATH)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM members WHERE username IN (?,?)', (username, username+'_b')).fetchone()[0], 0)

    def test_classification_backfill_idempotent_and_unknown_conservative(self):
        from modules.audit_retention import migrate_classification, cleanup_audit_rows
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('CREATE TABLE security_audit(id INTEGER PRIMARY KEY,action TEXT,outcome TEXT,details TEXT,created_at TEXT); CREATE TABLE invoice_jobs(audit_id INTEGER);')
            db.executemany('INSERT INTO security_audit VALUES(?,?,?,?,?)', [
                (1, 'member_profiles.account_preferences', 'success', '{}', '2000-01-01'),
                (2, 'unknown', 'success', '{}', '2999-01-01'),
                (3, 'member_profiles.account_preferences', 'failure', '{}', '2999-01-01')])
            migrate_classification(db)
            migrate_classification(db)
            self.assertEqual(db.execute('SELECT level FROM security_audit ORDER BY id').fetchall(), [('routine',), ('critical',), ('critical',)])
            with patch.dict('os.environ', {'AUDIT_MAX_ROWS': '1'}):
                self.assertEqual(cleanup_audit_rows(db)['ids'], [1])

    def test_default_audit_view_hides_routine(self):
        with closing(sqlite3.connect(app_module.DB_PATH)) as db, db:
            identifier = db.execute("INSERT INTO security_audit(action,method,target,outcome,created_at,level) VALUES('member_profiles.account_preferences','POST','/api/account/preferences','success','2999-01-01','routine')").lastrowid
        try:
            self.assertNotIn(identifier, [r['id'] for r in self.client.get('/api/admin/audit').get_json()['events']])
            self.assertIn(identifier, [r['id'] for r in self.client.get('/api/admin/audit?level=routine').get_json()['events']])
        finally:
            with closing(sqlite3.connect(app_module.DB_PATH)) as db, db:
                db.execute('DELETE FROM security_audit WHERE id=?', (identifier,))

