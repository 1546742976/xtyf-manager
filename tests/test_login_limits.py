"""Shared login counters remain atomic and successful users cannot erase them."""
import sqlite3
import unittest
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

from test_tasks import app_module


class LoginLimitTests(unittest.TestCase):
    def setUp(self):
        with closing(sqlite3.connect(app_module.DB_PATH)) as db, db:
            db.execute('DELETE FROM login_attempts')

    def tearDown(self):
        with closing(sqlite3.connect(app_module.DB_PATH)) as db, db:
            db.execute('DELETE FROM login_attempts')

    def test_success_only_clears_combination_counter(self):
        client = app_module.app.test_client()
        client.post('/login', json={'username':'unknown_login_limit','password':'bad'})
        result = client.post('/login', json={'username':'admin','password':'ci-test-password-only'})
        self.assertEqual(result.status_code, 200)
        with closing(sqlite3.connect(app_module.DB_PATH)) as db:
            keys = [row[0] for row in db.execute('SELECT attempt_key FROM login_attempts')]
        self.assertIn('global:', keys)
        self.assertTrue(any(key.startswith('ip:') for key in keys))

    def test_ip_limit_across_usernames(self):
        client = app_module.app.test_client()
        with patch.object(app_module, '_LOGIN_IP_LIMIT', 3, create=True):
            for index in range(3):
                response = client.post('/login', json={'username':f'unknown_{index}','password':'bad'})
            self.assertEqual(response.status_code, 429)
            self.assertGreater(int(response.headers['Retry-After']), 0)
            self.assertEqual(client.post('/login', json={'username':'admin','password':'ci-test-password-only'}).status_code, 429)

    def test_global_gate_allows_one_verification_per_second(self):
        client = app_module.app.test_client()
        with patch.object(app_module, '_LOGIN_GLOBAL_LIMIT', 1, create=True), patch.object(app_module.time, 'time', return_value=2000000000):
            client.post('/login', json={'username':'global_unknown','password':'bad'})
            first = client.post('/login', json={'username':'admin','password':'ci-test-password-only'})
            second = client.post('/login', json={'username':'admin','password':'ci-test-password-only'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.headers['Retry-After'], '1')


    def test_parallel_failures_increment_every_shared_bucket(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'counters.db')
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE login_attempts(attempt_key TEXT PRIMARY KEY,failures INTEGER,first_failed_at INTEGER,locked_until INTEGER)')
            def fail(_):
                with closing(sqlite3.connect(path, timeout=15)) as db:
                    db.row_factory = sqlite3.Row
                    app_module._failed_login(db, ('combination', 'ip:test', 'global:'), 2000000000)
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(fail, range(20)))
            with closing(sqlite3.connect(path)) as db:
                counts = [row[0] for row in db.execute('SELECT failures FROM login_attempts')]
            self.assertEqual(counts, [20, 20, 20])
