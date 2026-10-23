"""All active onboarded roles can create tasks without gaining management rights."""
import unittest
from uuid import uuid4

from test_tasks import app_module


class TaskCreationAccessTests(unittest.TestCase):
    def test_every_role_can_create_both_types_from_task_center(self):
        admin = app_module.app.test_client()
        self.assertEqual(admin.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only'}).status_code, 200)
        for role in ('admin', 'manager', 'member'):
            with self.subTest(role=role):
                name = 'creator_' + uuid4().hex[:12]
                member = admin.post('/api/member', json={
                    'username': name, 'name': name, 'password': 'create-password', 'role': role}).get_json()
                client = app_module.app.test_client()
                self.assertEqual(client.post('/login', json={
                    'username': name, 'password': 'create-password'}).status_code, 200)
                for mode in ('vehicle', 'daily'):
                    page = client.get('/tasks?type=' + mode)
                    self.assertEqual(page.status_code, 200)
                    self.assertIn('id="new-task-toggle"', page.get_data(as_text=True))
                    self.assertIn('daily-project-editor.js', page.get_data(as_text=True))
                vehicle = client.post('/api/task', json={'title': name, 'owner_id': member['id']})
                self.assertEqual(vehicle.status_code, 200, vehicle.get_json())
                daily = client.post('/api/daily-projects', json={
                    'title': name, 'groups': {'mechanical': [member['id']]}})
                self.assertEqual(daily.status_code, 200, daily.get_json())
                if role == 'member':
                    self.assertEqual(client.post('/api/daily-projects/' + str(daily.get_json()['id']),
                                                 json={}).status_code, 403)
                    self.assertEqual(client.post('/api/projects', json={'name': name}).status_code, 403)
                    rejected = client.post('/api/task', json={'title': 'legacy', 'project': name})
                    self.assertEqual(rejected.status_code, 400)

    def test_creation_still_requires_login_and_initial_password_change(self):
        client = app_module.app.test_client()
        for path in ('/api/task', '/api/daily-projects'):
            self.assertIn(client.post(path, json={'title': 'denied'}).status_code, (401, 403))
        admin = app_module.app.test_client()
        admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        name = 'initial_' + uuid4().hex[:12]
        admin.post('/api/member', json={'name': name, 'username': name, 'password': 'initial-password'})
        client.auto_onboard = False
        client.post('/login', json={'username': name, 'password': 'initial-password'})
        for path in ('/api/task', '/api/daily-projects'):
            response = client.post(path, json={'title': 'denied'})
            self.assertEqual(response.status_code, 403)
            self.assertIn('修改初始密码', response.get_json()['error'])
