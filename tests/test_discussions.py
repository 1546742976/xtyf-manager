"""Local collaboration entry and login boundaries through the Flask surface."""
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_tasks import app_module


class TestDiscussionPage(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        response = self.client.post('/login', json={
            'username': 'admin', 'password': '123456',
        })
        self.assertTrue(response.get_json()['ok'])

    def test_requires_login(self):
        visitor = app_module.app.test_client()
        response = visitor.get('/discussions')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.headers['Location'])
        self.assertEqual(visitor.get('/discussions/api/topics').status_code, 401)

    def test_local_page_does_not_depend_on_github_configuration_or_network(self):
        with patch.dict(app_module.app.config, {
            'GITHUB_REPOSITORY': 'invalid/repository/extra',
            'GITHUB_TOKEN': 'obsolete-secret-never-render',
            'GITHUB_WRITE_ENABLED': '1',
        }), patch('urllib.request.urlopen', side_effect=AssertionError('unexpected network')):
            response = self.client.get('/discussions')
            topics = self.client.get('/discussions/api/topics')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(topics.status_code, 200, topics.get_json())
        page = response.get_data(as_text=True)
        self.assertIn('协作', page)
        self.assertNotIn('obsolete-secret-never-render', page)
        self.assertNotIn('api.github.com', page)
        self.assertNotIn('github-new', page)

    def test_sidebar_entry_is_available_to_each_logged_in_role(self):
        for role in ('manager', 'member'):
            with self.subTest(role=role):
                username = f'local_discussion_{role}_{uuid4().hex[:10]}'
                password = 'discussion-test-password'
                created = self.client.post('/api/member', json={
                    'name': '协作入口' + role, 'username': username,
                    'password': password, 'role': role,
                })
                self.assertEqual(created.status_code, 200, created.get_json())
                client = app_module.app.test_client()
                self.assertTrue(client.post('/login', json={
                    'username': username, 'password': password,
                }).get_json()['ok'])
                response = client.get('/discussions')
                self.assertEqual(response.status_code, 200)
                self.assertIn('href="/discussions"', response.get_data(as_text=True))
                self.assertEqual(client.get('/discussions/api/topics').status_code, 200)


if __name__ == '__main__':
    unittest.main()
