"""Audit counterexamples through authenticated HTTP requests and isolated storage."""
import unittest
from uuid import uuid4
from unittest.mock import patch

from test_tasks import app_module


class TestAccountInputBounds(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.assertTrue(self.client.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only',
        }).get_json()['ok'])

    def test_creation_rejects_password_that_login_cannot_accept(self):
        response = self.client.post('/api/member', json={
            'name': '合成边界成员', 'username': 'bounds_' + uuid4().hex,
            'password': 'x' * 1025,
        })
        self.assertEqual(response.status_code, 400)

    def test_manager_cannot_reset_target_promoted_during_password_hash(self):
        password = 'synthetic-initial-password'
        ids = []
        for role in ('manager', 'member'):
            username = role + '_' + uuid4().hex
            created = self.client.post('/api/member', json={
                'name': '合成竞态成员', 'username': username,
                'password': password, 'role': role,
            }).get_json()
            ids.append(created['id'])
            if role == 'manager':
                manager = app_module.app.test_client()
                self.assertTrue(manager.post('/login', json={
                    'username': username, 'password': password,
                }).get_json()['ok'])
        original_hash = app_module.generate_password_hash

        def hash_with_promotion(value):
            promoted = self.client.post(f'/api/member/{ids[1]}/role', json={'role': 'admin'})
            self.assertEqual(promoted.status_code, 200)
            return original_hash(value)

        with patch.object(app_module, 'generate_password_hash', side_effect=hash_with_promotion):
            response = manager.post(f'/api/member/{ids[1]}/reset', json={'password': 'synthetic-new-password'})
        self.assertEqual(response.status_code, 403)
        target_client = app_module.app.test_client()
        target_client.auto_onboard = False
        self.assertEqual(target_client.post('/login', json={
            'username': username, 'password': 'synthetic-new-password',
        }).status_code, 400)
        self.assertTrue(target_client.post('/login', json={
            'username': username, 'password': password,
        }).get_json()['ok'])

    def test_password_types_and_maximum_round_trip(self):
        for value in (12345678, True, [], {}, 'short', 'x' * 1025):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(self.client.post('/api/member', json={
                    'name': '合成密码成员', 'username': uuid4().hex,
                    'password': value,
                }).status_code, 400)
        username = uuid4().hex
        # Leading/trailing whitespace is part of the password and must survive.
        password = ' ' + 'x' * 1022 + ' '
        self.assertEqual(self.client.post('/api/member', json={
            'name': '合成最大密码', 'username': username, 'password': password,
        }).status_code, 200)
        member = app_module.app.test_client()
        member.auto_onboard = False
        self.assertTrue(member.post('/login', json={'username': username, 'password': password}).get_json()['ok'])
        self.assertEqual(member.get('/api/data').status_code, 403)
        self.assertTrue(member.post('/api/password', json={'old': password, 'new': 'changed-password'}).get_json()['ok'])
        self.assertEqual(member.get('/api/data').status_code, 200)

    def test_actor_revoked_during_hash_cannot_reset_password(self):
        password = 'synthetic-initial-password'
        ids = []
        for role in ('manager', 'member'):
            username = uuid4().hex
            ids.append(self.client.post('/api/member', json={
                'name': '合成撤权成员', 'username': username,
                'password': password, 'role': role,
            }).get_json()['id'])
            if role == 'manager':
                manager = app_module.app.test_client()
                self.assertTrue(manager.post('/login', json={'username': username, 'password': password}).get_json()['ok'])
        original_hash = app_module.generate_password_hash

        def hash_with_revocation(value):
            self.assertEqual(self.client.post(f'/api/member/{ids[0]}/toggle').status_code, 200)
            return original_hash(value)

        with patch.object(app_module, 'generate_password_hash', side_effect=hash_with_revocation):
            response = manager.post(f'/api/member/{ids[1]}/reset', json={'password': 'synthetic-new-password'})
        self.assertEqual(response.status_code, 401)

    def test_invalid_role_and_oversized_ids_are_client_errors(self):
        for role in ([], {}, True):
            with self.subTest(role=role):
                response = self.client.post('/api/member/999999/role', json={'role': role})
                self.assertEqual(response.status_code, 400)
        huge = 2**63
        for path, method in ((f'/api/member/{huge}/reset', 'post'),
                             (f'/api/assets/units/{huge}', 'get'),
                             (f'/api/assets/units/{huge}/history', 'get'),
                             (f'/funds/api/records/{huge}', 'get')):
            with self.subTest(path=path):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 400)
        for path in ('/api/assets/units/9223372036854775807', '/funds/api/records/9223372036854775807'):
            self.assertEqual(self.client.get(path).status_code, 404)
