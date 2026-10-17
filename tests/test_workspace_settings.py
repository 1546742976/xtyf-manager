"""Eight palettes, account isolation and the shared Beijing-calendar target."""
from datetime import date, datetime, timezone
import json
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_tasks import app_module
from modules import workspace_settings as settings


class TestWorkspaceSettings(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': '123456'})
        self.name = 'settings_' + uuid4().hex[:12]
        self.password = 'settings-member-password'
        result = self.admin.post('/api/member', json={
            'name': '外观设置测试', 'username': self.name, 'password': self.password})
        self.assertEqual(result.status_code, 200)
        self.member = self.member_login()
        self.member_id = self.member.get('/api/account/profile').get_json()['profile']['id']
        self.before_countdown = self.admin.get('/api/workspace/countdown').get_json()['countdown']
        self.before_preferences = self.admin.get('/api/account/preferences').get_json()

    def tearDown(self):
        current = self.admin.get('/api/workspace/countdown').get_json()['countdown']
        if (current['title'], current['target_date']) != (
                self.before_countdown['title'], self.before_countdown['target_date']):
            self.admin.put('/api/workspace/countdown', json={
                'title': self.before_countdown['title'],
                'target_date': self.before_countdown['target_date'], 'version': current['version']})
        self.admin.post('/api/account/preferences', json={
            key: self.before_preferences[key] for key in ('mode', 'light_theme', 'dark_theme')})

    def member_login(self):
        client = app_module.app.test_client()
        self.assertEqual(client.post('/login', json={
            'username': self.name, 'password': self.password}).status_code, 200)
        return client

    def test_all_palettes_persist_across_sessions_without_revoking_login(self):
        other_device = self.member_login()
        session_version = self.member.get('/api/data').get_json()['me']['session_version']
        for light in sorted(settings.LIGHT_THEMES):
            for dark in sorted(settings.DARK_THEMES):
                body = {'mode': 'system', 'light_theme': light, 'dark_theme': dark}
                response = self.member.post('/api/account/preferences', json=body)
                self.assertEqual(response.status_code, 200)
                saved = other_device.get('/api/account/preferences').get_json()
                self.assertEqual({key: saved[key] for key in body}, body)
                self.assertEqual(saved['theme'], 'light')
        self.assertEqual(self.member_login().get('/api/account/preferences').get_json()['mode'], 'system')
        self.assertEqual(other_device.get('/api/data').get_json()['me']['session_version'], session_version)
        self.assertEqual(self.admin.get('/api/account/preferences').get_json(), self.before_preferences)

    def test_legacy_switch_preserves_palettes_and_exits_system(self):
        self.member.post('/api/account/preferences', json={
            'mode': 'system', 'light_theme': 'paw', 'dark_theme': 'xueli'})
        response = self.member.post('/api/account/preferences', json={'theme': 'dark'})
        self.assertEqual(response.get_json(), {
            'ok': True, 'mode': 'dark', 'theme': 'dark', 'light_theme': 'paw', 'dark_theme': 'xueli'})
        self.member.post('/api/account/preferences', json={
            'mode': 'system', 'light_theme': 'silver', 'dark_theme': 'graphite'})
        self.assertEqual(self.member.get('/api/account/preferences').get_json()['theme'], 'dark')

    def test_invalid_preference_requests_are_atomic_and_cannot_target_others(self):
        valid = {'mode': 'dark', 'light_theme': 'paw', 'dark_theme': 'xueli'}
        previous = self.member.get('/api/account/preferences').get_json()
        invalid = [dict(valid, member_id=1), dict(valid, role='admin'), dict(valid, theme='dark'),
                   dict(valid, mode='automatic'), dict(valid, light_theme='space'),
                   dict(valid, dark_theme='paw'), dict(valid, mode=True),
                   dict(valid, light_theme=[]), {'mode': 'dark'}, {'theme': 'xueli'}]
        for body in invalid:
            with self.subTest(body=body):
                self.assertEqual(self.member.post('/api/account/preferences', json=body).status_code, 400)
                self.assertEqual(self.member.get('/api/account/preferences').get_json(), previous)
        self.assertEqual(self.member.post('/api/account/preferences', json=valid,
                                         headers={'X-CSRF-Token': 'invalid'}).status_code, 403)
        self.assertEqual(self.member.get('/api/account/preferences').get_json(), previous)
        anonymous = app_module.app.test_client()
        self.assertEqual(anonymous.post('/api/account/preferences', json=valid).status_code, 401)

    def test_countdown_read_permissions_and_admin_only_write_with_audit(self):
        initial = self.member.get('/api/workspace/countdown').get_json()['countdown']
        self.assertNotIn('id="countdown-config"', self.member.get('/').get_data(as_text=True))
        self.assertIn('id="countdown-config"', self.admin.get('/').get_data(as_text=True))
        body = {'title': '完整形态考核', 'target_date': '2026-12-12', 'version': initial['version']}
        self.assertEqual(self.member.put('/api/workspace/countdown', json=body).status_code, 403)
        self.admin.post(f'/api/member/{self.member_id}/profile', json={'role': 'manager'})
        manager = self.member_login()
        self.assertEqual(manager.put('/api/workspace/countdown', json=body).status_code, 403)
        self.assertEqual(manager.get('/api/workspace/countdown').status_code, 200)
        self.assertEqual(self.admin.put('/api/workspace/countdown', json=body,
                                       headers={'X-CSRF-Token': 'invalid'}).status_code, 403)
        saved = self.admin.put('/api/workspace/countdown', json=body)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()['countdown']['title'], body['title'])
        self.assertTrue(saved.get_json()['countdown']['configured'])
        with sqlite3.connect(app_module.DB_PATH) as db:
            action, outcome, details = db.execute(
                "SELECT action,outcome,details FROM security_audit WHERE target=? ORDER BY id DESC LIMIT 1",
                ('/api/workspace/countdown',)).fetchone()
        self.assertEqual(action, 'workspace_settings.workspace_countdown')
        self.assertEqual(outcome, 'success')
        self.assertEqual(json.loads(details)['countdown']['target_date'], body['target_date'])
        anonymous = app_module.app.test_client()
        self.assertEqual(anonymous.get('/api/workspace/countdown').status_code, 401)
        self.assertEqual(anonymous.put('/api/workspace/countdown', json=body).status_code, 401)

    def test_countdown_versions_and_clear(self):
        version = self.before_countdown['version']
        first = {'title': '目标 A', 'target_date': '2026-12-01', 'version': version}
        saved = self.admin.put('/api/workspace/countdown', json=first).get_json()['countdown']
        conflict = self.admin.put('/api/workspace/countdown', json=dict(first, title='目标 B'))
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()['countdown']['title'], '目标 A')
        self.assertEqual(saved['version'], version + 1)
        cleared = self.admin.put('/api/workspace/countdown', json={
            'title': '', 'target_date': '', 'version': saved['version']}).get_json()['countdown']
        self.assertFalse(cleared['configured'])
        self.assertIsNone(cleared['days_left'])
        self.assertEqual(cleared['version'], saved['version'] + 1)

    def test_countdown_invalid_configuration_does_not_modify_state(self):
        body = {'title': '考核', 'target_date': '2026-12-01', 'version': self.before_countdown['version']}
        invalid = [dict(body, title=''), dict(body, target_date=''), dict(body, title='a' * 81),
                   dict(body, title='hello\x00world'), dict(body, target_date='2026-02-30'),
                   dict(body, target_date='2026-1-1'), dict(body, target_date='2026-12-01T00:00:00'),
                   dict(body, target_date=None), dict(body, version=True), dict(body, version=1.5),
                   dict(body, version=0), dict(body, version=2**63), dict(body, enabled=True)]
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                self.assertEqual(self.admin.put('/api/workspace/countdown', json=candidate).status_code, 400)
                self.assertEqual(self.admin.get('/api/workspace/countdown').get_json()['countdown'],
                                 self.before_countdown)


class TestWorkspaceMigrationAndCalendar(unittest.TestCase):
    def test_migration_preserves_legacy_dark_and_new_choices_on_repeat(self):
        db = sqlite3.connect(':memory:')
        db.execute("CREATE TABLE members(id INTEGER PRIMARY KEY,ui_theme TEXT NOT NULL DEFAULT 'light' "
                   "CHECK(ui_theme IN ('light','dark')))")
        db.executemany('INSERT INTO members(id,ui_theme) VALUES(?,?)', [(1, 'dark'), (2, 'light')])
        db.commit()
        settings.migrate(db)
        self.assertEqual(db.execute('SELECT ui_theme,theme_mode,light_theme,dark_theme FROM members WHERE id=1')
                         .fetchone(), ('dark', 'dark', 'silver', 'space'))
        db.execute("UPDATE members SET theme_mode='system',light_theme='paw',dark_theme='xueli' WHERE id=1")
        db.execute("UPDATE workspace_countdown SET title='目标',target_date='2028-02-29',version=7")
        db.commit()
        settings.migrate(db)
        self.assertEqual(db.execute('SELECT theme_mode,light_theme,dark_theme FROM members WHERE id=1')
                         .fetchone(), ('system', 'paw', 'xueli'))
        self.assertEqual(db.execute('SELECT title,target_date,version FROM workspace_countdown').fetchall(),
                         [('目标', '2028-02-29', 7)])
        db.execute('INSERT INTO members(id) VALUES(3)')
        self.assertEqual(db.execute('SELECT theme_mode,light_theme,dark_theme FROM members WHERE id=3').fetchone(),
                         ('light', 'silver', 'space'))
        db.close()

    def test_calendar_days_today_past_leap_and_shanghai_boundary(self):
        for target, today, expected in [('2026-10-16', date(2026, 10, 16), 0),
                                        ('2026-10-15', date(2026, 10, 16), -1),
                                        ('2026-10-19', date(2026, 10, 16), 3),
                                        ('2028-03-01', date(2028, 2, 28), 2)]:
            actual = settings.countdown_payload({'title': '考核', 'target_date': target, 'version': 1}, today)
            self.assertEqual(actual['days_left'], expected)
        utc_instant = datetime(2026, 10, 15, 16, 1, tzinfo=timezone.utc)
        with patch.object(settings, 'datetime') as clock:
            clock.now.side_effect = lambda tz: utc_instant.astimezone(tz)
            self.assertEqual(settings.beijing_today(), date(2026, 10, 16))


if __name__ == '__main__':
    unittest.main()
