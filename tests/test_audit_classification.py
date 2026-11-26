"""Audit grouping, retention and upgrade behavior (synthetic data only)."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from modules.audit_retention import (
    classify_action, cleanup_plan, migrate_classification,
    migrate_retention_settings, retention_policy, save_retention_days,
    validate_retention_days,
)


def fake_db():
    db = sqlite3.connect(':memory:')
    db.executescript("""
        CREATE TABLE security_audit (
            id INTEGER PRIMARY KEY, action TEXT NOT NULL, outcome TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
        );
        CREATE TABLE invoice_jobs (audit_id INTEGER);
    """)
    return db


class AuditClassificationTests(unittest.TestCase):
    def test_categories_and_severity(self):
        cases = {
            ('funds.delete_record', 'success'): ('finance', 'permanent'),
            ('funds.delete_record', 'failure'): ('finance', 'permanent'),
            ('invoices.api_upload', 'success'): ('finance', 'permanent'),
            ('invoices.job.refresh', 'error'): ('finance', 'permanent'),
            ('security.permissions_set', 'success'): ('security', 'critical'),
            ('client_api.login', 'failure'): ('security', 'critical'),
            ('assets.operate', 'success'): ('assets', 'important'),
            ('task_catalogs.member_create', 'success'): ('members', 'important'),
            ('task_catalogs.groups_delete', 'success'): ('tasks', 'important'),
            ('task_catalogs.projects_edit', 'success'): ('tasks', 'normal'),
            ('workspace_settings.workspace_countdown', 'success'): ('settings', 'routine'),
            ('member_profiles.account_preferences', 'failure'): ('settings', 'critical'),
            ('unknown_endpoint', 'success'): ('other', 'critical'),
        }
        for (action, outcome), expected in cases.items():
            with self.subTest(action=action, outcome=outcome):
                self.assertEqual(classify_action(action, outcome), expected)

    def test_migration_backfills_once_preserving_finance(self):
        with closing(fake_db()) as db:
            db.executemany(
                'INSERT INTO security_audit(id,action,outcome,created_at) VALUES(?,?,?,?)',
                [(1, 'funds.delete_record', 'success', '2000-01-01'),
                 (2, 'invoices.job.organize', 'failure', '2000-01-01'),
                 (3, 'member_profiles.account_preferences', 'success', '2000-01-01'),
                 (4, 'some_future_feature', 'success', '2000-01-01')])
            migrate_classification(db)
            first = db.execute('SELECT category,level FROM security_audit ORDER BY id').fetchall()
            self.assertEqual(first, [('finance', 'permanent'), ('finance', 'permanent'),
                                     ('settings', 'routine'), ('other', 'critical')])
            migrate_classification(db)
            self.assertEqual(db.execute('SELECT category,level FROM security_audit ORDER BY id').fetchall(), first)

    def test_permanent_finance_survives_expiry_and_cap_even_if_legacy_level(self):
        with closing(fake_db()) as db:
            db.executemany(
                'INSERT INTO security_audit(id,action,outcome,created_at) VALUES(?,?,?,?)',
                [(1, 'funds.create_record', 'success', '2000-01-01'),
                 (2, 'invoices.api_export_download', 'success', '2000-01-01'),
                 (3, 'workspace_settings.workspace_countdown', 'success', '2000-01-01'),
                 (4, 'task_catalogs.projects_edit', 'success', '2000-01-01')])
            migrate_classification(db)
            # Simulate old writer assigning "critical" to a financial event.
            db.execute("UPDATE security_audit SET level='critical' WHERE id=2")
            with patch.dict('os.environ', {'AUDIT_RETENTION_DAYS':'1',
                      'AUDIT_IMPORTANT_RETENTION_DAYS':'1', 'AUDIT_NORMAL_RETENTION_DAYS':'1',
                      'AUDIT_ROUTINE_RETENTION_DAYS':'1', 'AUDIT_MAX_ROWS':'1'}):
                plan = cleanup_plan(db)
            self.assertEqual(plan['ids'], [3, 4])
            self.assertNotIn(1, plan['ids'])
            self.assertNotIn(2, plan['ids'])

    def test_pending_invoice_job_and_request_chain_survive(self):
        with closing(fake_db()) as db:
            db.executemany(
                'INSERT INTO security_audit(id,action,outcome,details,created_at) VALUES(?,?,?,?,?)',
                [(1, 'task_catalogs.projects_edit', 'success', '{}', '2000-01-01'),
                 (2, 'invoices.job.organize', 'success', '{"request_audit_id":1}', '2000-01-01'),
                 (3, 'task_catalogs.projects_edit', 'pending', '{}', '2000-01-01')])
            db.execute('INSERT INTO invoice_jobs(audit_id) VALUES(2)')
            migrate_classification(db)
            with patch.dict('os.environ', {'AUDIT_NORMAL_RETENTION_DAYS':'1',
                                            'AUDIT_MAX_ROWS':'1'}):
                plan = cleanup_plan(db)
            self.assertEqual(plan['ids'], [])


class AuditRetentionSettingsTests(unittest.TestCase):
    def test_file_environment_admin_precedence_and_reset(self):
        with closing(fake_db()) as db, tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / 'audit.json'
            config_path.write_text(json.dumps({'critical': 400, 'routine': 0}), encoding='utf-8')
            with patch.dict(os.environ, {
                'AUDIT_RETENTION_CONFIG': str(config_path),
                'AUDIT_RETENTION_DAYS': '45',
                'AUDIT_NORMAL_RETENTION_DAYS': '30',
            }):
                policy = retention_policy(db)
                self.assertEqual(policy['days'], {
                    'permanent': None, 'critical': 400, 'important': 180,
                    'normal': 30, 'routine': 0,
                })
                self.assertEqual(policy['sources']['critical'], 'file')
                self.assertEqual(policy['sources']['normal'], 'environment')
                migrate_retention_settings(db)
                migrate_retention_settings(db)
                save_retention_days(db, {
                    'critical': 0, 'important': 999, 'normal': 15, 'routine': 3,
                })
                policy = retention_policy(db)
                self.assertEqual(policy['days']['permanent'], None)
                self.assertEqual(policy['days']['critical'], 0)
                self.assertEqual(policy['days']['important'], 999)
                self.assertTrue(all(policy['sources'][k] == 'admin'
                                    for k in ('critical', 'important', 'normal', 'routine')))
                save_retention_days(db, reset=True)
                policy = retention_policy(db)
                self.assertEqual(policy['days']['critical'], 400)
                self.assertEqual(policy['days']['routine'], 0)

    def test_invalid_configuration_never_permits_finance_override(self):
        for data in ({'permanent': 1}, {'routine': -1}, {'normal': 2.3},
                     {'normal': True}, {'routine': 40000}, {'missing': 10}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_retention_days(data)
        with closing(fake_db()) as db, tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'audit.json'
            path.write_text('{"permanent": 0}', encoding='utf-8')
            with patch.dict(os.environ, {'AUDIT_RETENTION_CONFIG': str(path)}):
                with self.assertRaises(ValueError):
                    cleanup_plan(db)

    def test_zero_prevents_routine_capacity_eviction(self):
        with closing(fake_db()) as db:
            db.executemany(
                'INSERT INTO security_audit(id,action,outcome,created_at) VALUES(?,?,?,?)',
                [(1, 'workspace_settings.workspace_countdown', 'success', '2000-01-01'),
                 (2, 'workspace_settings.workspace_countdown', 'success', '2000-01-01')])
            migrate_classification(db)
            with patch.dict(os.environ, {
                'AUDIT_ROUTINE_RETENTION_DAYS': '0', 'AUDIT_MAX_ROWS': '1',
            }):
                self.assertEqual(cleanup_plan(db)['ids'], [])
            with patch.dict(os.environ, {
                'AUDIT_ROUTINE_RETENTION_DAYS': '1', 'AUDIT_MAX_ROWS': '1',
            }):
                self.assertEqual(cleanup_plan(db)['ids'], [1, 2])


class AuditApiFilterTests(unittest.TestCase):
    def setUp(self):
        from test_tasks import app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()
        response = self.client.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only'})
        self.assertTrue(response.get_json()['ok'])
        self.inserted = []
        with closing(sqlite3.connect(app_module.DB_PATH)) as db, db:
            for category, level, outcome in [
                ('finance', 'permanent', 'success'),
                ('assets', 'important', 'failure'),
                ('tasks', 'normal', 'success'),
                ('settings', 'routine', 'success'),
            ]:
                identifier = db.execute(
                    """INSERT INTO security_audit
                       (action,method,target,details,outcome,created_at,category,level)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    ('test.filter.' + category, 'POST', '/test', '{}', outcome,
                     '2999-01-01 00:00:00', category, level)
                ).lastrowid
                self.inserted.append(identifier)

    def tearDown(self):
        with closing(sqlite3.connect(self.app_module.DB_PATH)) as db, db:
            db.executemany('DELETE FROM security_audit WHERE id=?',
                           ((identifier,) for identifier in self.inserted))

    def test_default_and_combined_filters(self):
        def ids(path):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            return {row['id'] for row in response.get_json()['events']}

        priority = ids('/api/admin/audit')
        self.assertIn(self.inserted[0], priority)
        self.assertIn(self.inserted[1], priority)
        self.assertNotIn(self.inserted[2], priority)
        self.assertNotIn(self.inserted[3], priority)
        self.assertIn(self.inserted[0], ids('/api/admin/audit?category=finance&level=permanent'))
        self.assertNotIn(self.inserted[1], ids('/api/admin/audit?category=finance&level=permanent'))
        self.assertIn(self.inserted[1], ids('/api/admin/audit?category=assets&level=important&outcome=failure'))
        self.assertNotIn(self.inserted[0], ids('/api/admin/audit?category=assets&level=important&outcome=failure'))
        self.assertIn(self.inserted[3], ids('/api/admin/audit?level=all&category=settings'))
        for query in ('category=invalid', 'level=invalid', 'outcome=invalid'):
            self.assertEqual(self.client.get('/api/admin/audit?' + query).status_code, 400)


class AuditAdminPolicyApiTests(unittest.TestCase):
    def setUp(self):
        from test_tasks import app_module
        self.app_module = app_module
        self.client = app_module.app.test_client()
        login = self.client.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only'})
        self.assertTrue(login.get_json()['ok'])
        with closing(sqlite3.connect(app_module.DB_PATH)) as db:
            migrate_retention_settings(db)
            self.previous = db.execute(
                'SELECT level,days FROM audit_retention_settings').fetchall()

    def tearDown(self):
        with closing(sqlite3.connect(self.app_module.DB_PATH)) as db, db:
            db.execute('DELETE FROM audit_retention_settings')
            db.executemany('INSERT INTO audit_retention_settings(level,days) VALUES(?,?)',
                           self.previous)

    def test_admin_edit_and_restore_cannot_touch_permanent(self):
        url = '/api/admin/audit-retention'
        initial = self.client.get(url)
        self.assertEqual(initial.status_code, 200)
        self.assertIsNone(initial.get_json()['days']['permanent'])
        for payload in (
            {'days': {'permanent': 1}},
            {'days': {'critical': 20}},
            {'days': {'critical': 1, 'important': 1, 'normal': 1, 'routine': -1}},
            {'reset': True, 'days': {}},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(url, json=payload).status_code, 400)
        chosen = {'critical': 0, 'important': 500, 'normal': 31, 'routine': 8}
        response = self.client.post(url, json={'days': chosen})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['days'], {'permanent': None, **chosen})
        self.assertTrue(all(response.get_json()['sources'][key] == 'admin'
                            for key in chosen))
        # A new client reads the same persisted settings.
        self.assertEqual(self.client.get(url).get_json()['days']['important'], 500)
        with closing(sqlite3.connect(self.app_module.DB_PATH)) as db:
            rows = db.execute('SELECT level,days FROM audit_retention_settings').fetchall()
            self.assertEqual(dict(rows), chosen)
        reset = self.client.post(url, json={'reset': True})
        self.assertEqual(reset.status_code, 200)
        self.assertIsNone(reset.get_json()['days']['permanent'])
        self.assertNotEqual(self.client.get(url).get_json()['sources']['critical'], 'admin')

    def test_policy_endpoints_require_admin(self):
        visitor = self.app_module.app.test_client()
        self.assertNotEqual(visitor.get('/api/admin/audit-retention').status_code, 200)
        self.assertNotEqual(visitor.post('/api/admin/audit-retention', json={
            'days': {'critical': 1, 'important': 1, 'normal': 1, 'routine': 1},
        }).status_code, 200)


if __name__ == '__main__':
    unittest.main()
