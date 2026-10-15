"""Account-scoped themes, session continuity and server-rendered first paint."""
import sqlite3
import unittest
from uuid import uuid4

from test_tasks import app_module
from test_migrations_collaboration import BASE_SCHEMA
from modules.task_migrations import migrate


class TestAccountPreferences(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        self.username = 'theme_' + uuid4().hex[:12]
        self.password = 'theme-member-password'
        self.admin.post('/api/member', json={'name': '主题测试', 'username': self.username, 'password': self.password})
        self.member = self.login_member()

    def tearDown(self):
        self.admin.post('/api/account/preferences', json={'theme': 'light'})

    def login_member(self):
        client = app_module.app.test_client()
        self.assertEqual(client.post('/login', json={'username': self.username, 'password': self.password}).status_code, 200)
        return client

    def test_default_save_other_device_relogin_and_account_isolation(self):
        other_device = self.login_member()
        self.assertEqual(self.member.get('/api/account/preferences').get_json(), {'ok': True, 'theme': 'light'})
        before = self.member.get('/api/data').get_json()['me']['session_version']
        self.assertEqual(self.member.post('/api/account/preferences', json={'theme': 'dark'}).get_json(), {'ok': True, 'theme': 'dark'})
        self.assertEqual(other_device.get('/api/account/preferences').get_json()['theme'], 'dark')
        self.assertEqual(other_device.get('/api/data').get_json()['me']['session_version'], before)
        self.assertIn('data-theme="dark"', self.login_member().get('/tasks').get_data(as_text=True))
        self.assertEqual(self.admin.get('/api/account/preferences').get_json()['theme'], 'light')

    def test_server_renders_theme_in_all_shells_and_login_stays_light(self):
        self.admin.post('/api/account/preferences', json={'theme': 'dark'})
        for url in ('/', '/tasks', '/manage', '/members', '/funds/', '/invoices/', '/account', '/discussions', '/admin/security'):
            with self.subTest(url=url):
                response = self.admin.get(url)
                self.assertEqual(response.status_code, 200)
                text = response.get_data(as_text=True)
                self.assertIn('data-theme="dark"', text)
                self.assertIn('content="#0b0b0c"', text)
                self.assertIn('theme.js', text)
        anonymous = app_module.app.test_client()
        self.assertIn('data-theme="light"', anonymous.get('/login').get_data(as_text=True))

    def test_roles_can_only_save_their_own_theme(self):
        member_id = self.member.get('/api/account/profile').get_json()['profile']['id']
        for role in ('member', 'manager', 'admin'):
            self.admin.post(f'/api/member/{member_id}/profile', json={'role': role})
            client = self.login_member()
            self.assertEqual(client.post('/api/account/preferences', json={'theme': 'dark'}).status_code, 200)
            self.assertEqual(client.post('/api/account/preferences', json={'theme': 'light', 'member_id': 1}).status_code, 400)

    def test_invalid_payload_csrf_and_anonymous_cannot_update(self):
        for data in ({}, {'theme': 'system'}, {'theme': None}, {'theme': 1}, {'theme': []}):
            self.assertEqual(self.member.post('/api/account/preferences', json=data).status_code, 400)
        self.assertEqual(self.member.post('/api/account/preferences', json={'theme': 'dark'}, headers={'X-CSRF-Token': 'invalid'}).status_code, 403)
        anonymous = app_module.app.test_client()
        self.assertEqual(anonymous.get('/api/account/preferences').status_code, 401)
        self.assertEqual(anonymous.post('/api/account/preferences', json={'theme': 'dark'}).status_code, 401)
        self.assertEqual(self.member.get('/api/account/preferences').get_json()['theme'], 'light')

    def test_repeat_safe_migration_preserves_saved_theme_and_dates(self):
        db = sqlite3.connect(':memory:')
        db.executescript(BASE_SCHEMA)
        db.execute("INSERT INTO members(id,name,group_name) VALUES (1,'test','机械')")
        db.execute("INSERT INTO tasks(id,start_date,due_date) VALUES (1,'2026-10-14','2026-10-22')")
        db.commit()
        migrate(db)
        self.assertEqual(db.execute('SELECT ui_theme FROM members').fetchone()[0], 'light')
        db.execute("UPDATE members SET ui_theme='dark'")
        db.execute("INSERT INTO task_progress_updates(task_id,actor_id,actor_name,progress,note,created_at) "
                   "VALUES (1,1,'test',45,'保留上游进度记录','2026-10-15')")
        db.commit()
        migrate(db)
        self.assertEqual(db.execute('SELECT ui_theme FROM members').fetchone()[0], 'dark')
        self.assertEqual(db.execute('SELECT start_date,due_date FROM tasks').fetchone(), ('2026-10-14','2026-10-22'))
        self.assertEqual(db.execute('SELECT progress,note FROM task_progress_updates').fetchone(),
                         (45, '保留上游进度记录'))
        self.assertIn('progress_update_id', {row[1] for row in db.execute('PRAGMA table_info(task_attachments)')})
        db.close()
