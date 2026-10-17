"""登录限流、会话失效回归。"""
import unittest
from uuid import uuid4
from test_tasks import app_module


class TestAuthSecurity(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        response = self.admin.post('/login', json={
            'username': 'admin', 'password': '123456'
        })
        self.assertTrue(response.get_json()['ok'])
        self.username = 'auth_' + uuid4().hex[:12]
        self.password = 'example-test-member-pass'
        created = self.admin.post('/api/member', json={
            'name': '认证测试成员', 'username': self.username,
            'password': self.password
        })
        self.assertTrue(created.get_json()['ok'])
        self.uid = created.get_json()['id']

    def _login(self, client, password=None):
        return client.post('/login', json={
            'username': self.username,
            'password': self.password if password is None else password
        })

    def test_too_many_failed_logins_lock_correct_password(self):
        client = app_module.app.test_client()
        for i in range(6):
            response = self._login(client, 'wrong-password')
            self.assertEqual(response.status_code, 429 if i == 5 else 400)
        self.assertEqual(self._login(client).status_code, 429)

    def test_password_reset_revokes_existing_session(self):
        member = app_module.app.test_client()
        self.assertTrue(self._login(member).get_json()['ok'])
        self.assertEqual(member.get('/api/data').status_code, 200)
        result = self.admin.post('/api/member/%d/reset' % self.uid, json={})
        self.assertTrue(result.get_json()['ok'])
        self.assertEqual(member.get('/api/data').status_code, 401)
        self.assertEqual(self._login(member).status_code, 400)
        self.assertTrue(self._login(member, result.get_json()['initial_password']).get_json()['ok'])

    def test_password_change_keeps_current_session_only(self):
        first, second = app_module.app.test_client(), app_module.app.test_client()
        self.assertTrue(self._login(first).get_json()['ok'])
        self.assertTrue(self._login(second).get_json()['ok'])
        changed = first.post('/api/password', json={
            'old': self.password, 'new': 'another-good-password'
        })
        self.assertTrue(changed.get_json()['ok'])
        self.assertEqual(first.get('/api/data').status_code, 200)
        self.assertEqual(second.get('/api/data').status_code, 401)

    def test_disabling_member_revokes_session(self):
        client = app_module.app.test_client()
        self.assertTrue(self._login(client).get_json()['ok'])
        result = self.admin.post('/api/member/%d/toggle' % self.uid, json={})
        self.assertTrue(result.get_json()['ok'])
        self.assertEqual(client.get('/api/data').status_code, 401)


if __name__ == '__main__':
    unittest.main()
