"""Role-inherited administration stays consistent across HTTP and background work."""
import unittest
from uuid import uuid4

from test_tasks import app_module
from common import get_db
from modules import invoice_jobs
from modules.security import PERMISSIONS, has_permission


class SuperadminTests(unittest.TestCase):
    def setUp(self):
        self.owner = app_module.app.test_client()
        self.owner.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        username = 'super_' + uuid4().hex[:12]
        self.mid = self.owner.post('/api/member', json={'name': username, 'username': username, 'password': 'synthetic-password', 'role': 'admin'}).get_json()['id']
        self.client = app_module.app.test_client()
        self.client.post('/login', json={'username': username, 'password': 'synthetic-password'})

    def test_role_grants_work_without_explicit_rows_and_cannot_be_revoked(self):
        with app_module.app.app_context():
            self.assertEqual(get_db().execute('SELECT count(*) FROM member_permissions WHERE member_id=?', (self.mid,)).fetchone()[0], 0)
        boot = self.client.get('/api/client/bootstrap').get_json()
        self.assertEqual(boot['role_names']['admin'], '超级管理员')
        for key in ('review_funds', 'manage_invoices', 'manage_assets', 'manage_security'):
            self.assertTrue(boot['permissions'][key], key)
        listed = next(m for m in self.owner.get('/api/admin/permissions').get_json()['members'] if m['id'] == self.mid)
        self.assertEqual(set(listed['permissions']), set(PERMISSIONS))
        self.assertEqual(set(listed['inherited_permissions']), set(PERMISSIONS))
        self.assertEqual(listed['explicit_permissions'], [])
        self.assertEqual(self.owner.post(f'/api/admin/permissions/{self.mid}', json={'permissions': []}).status_code, 409)
        self.assertEqual(self.client.get('/invoices/api/jobs').status_code, 200)
        record = self.client.post('/funds/api/records', json={
            'record_type': '进', 'amount': '1.00', 'record_date': '2026-10-24'}).get_json()
        self.assertEqual(record['code'], 0)
        self.assertEqual(record['review_status'], '已审核')

    def test_demotion_removes_only_inherited_grants(self):
        with app_module.app.app_context():
            db = get_db()
            db.execute('INSERT INTO member_permissions VALUES(?,?,?,?)', (self.mid, 'invoices.manage', 1, '2026-10-24 00:00:00'))
            db.commit()
        self.owner.post(f'/api/member/{self.mid}/role', json={'role': 'manager'})
        self.assertEqual(self.client.get('/api/client/bootstrap').get_json()['authenticated'], False)
        with app_module.app.app_context():
            user = dict(get_db().execute('SELECT * FROM members WHERE id=?', (self.mid,)).fetchone())
            self.assertTrue(has_permission('invoices.manage', user))
            self.assertFalse(has_permission('funds.review', user))
            self.assertFalse(has_permission('unknown.permission', user))

    def test_background_viewer_rechecks_role_and_account_state(self):
        with app_module.app.app_context():
            version = get_db().execute('SELECT session_version FROM members WHERE id=?', (self.mid,)).fetchone()[0]
        self.assertTrue(invoice_jobs.actor_is_current(self.mid, version))
        self.assertFalse(invoice_jobs.actor_is_current(self.mid, version + 1))
        for column, invalid, valid in [('is_active', 0, 1), ('must_change_password', 1, 0), ('deleted_at', '2026-10-24', None), ('role', 'manager', 'admin')]:
            with app_module.app.app_context():
                db = get_db(); db.execute(f'UPDATE members SET {column}=? WHERE id=?', (invalid, self.mid)); db.commit()
            self.assertFalse(invoice_jobs.actor_is_current(self.mid, version), column)
            with app_module.app.app_context():
                db = get_db(); db.execute(f'UPDATE members SET {column}=? WHERE id=?', (valid, self.mid)); db.commit()

    def test_forced_password_and_inactive_admin_cannot_use_role_grants(self):
        with app_module.app.app_context():
            db = get_db(); db.execute('UPDATE members SET must_change_password=1 WHERE id=?', (self.mid,)); db.commit()
        self.assertEqual(self.client.get('/api/admin/permissions').status_code, 403)
        self.assertFalse(any(self.client.get('/api/client/bootstrap').get_json()['permissions'].values()))
        with app_module.app.app_context():
            db = get_db(); db.execute('UPDATE members SET is_active=0 WHERE id=?', (self.mid,)); db.commit()
        self.assertEqual(self.client.get('/api/admin/permissions').status_code, 401)
