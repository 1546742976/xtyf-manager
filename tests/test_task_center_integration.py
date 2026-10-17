"""The common entry preserves two distinct workflows and safe mail deep links."""
import unittest
from uuid import uuid4

from test_tasks import app_module


class TaskCenterIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = app_module.app.test_client()
        cls.admin.post('/login', json={'username': 'admin', 'password': '123456'})
        name = 'tabs_' + uuid4().hex[:10]
        cls.admin.post('/api/member', json={'name': name, 'username': name, 'password': 'member-password'})
        cls.member = app_module.app.test_client()
        cls.member.post('/login', json={'username': name, 'password': 'member-password'})

    def test_only_active_workflow_is_rendered(self):
        vehicle = self.admin.get('/tasks').get_data(as_text=True)
        daily = self.admin.get('/tasks?type=daily&project=12').get_data(as_text=True)
        self.assertIn('id="board"', vehicle)
        self.assertIn('id="task-create-panel"', vehicle)
        self.assertNotIn('id="daily-app"', vehicle)
        self.assertIn('id="daily-app"', daily)
        self.assertIn('data-project-id="12"', daily)
        self.assertNotIn('id="board"', daily)
        self.assertIn('id="create-form"', daily)
        for html in (vehicle, daily):
            self.assertEqual(html.count('id="task-create-panel"'), 1)
            self.assertEqual(html.count('id="task-create-type"'), 1)
            self.assertEqual(html.count('id="new-task-toggle"'), 1)
            self.assertEqual(html.count('task-create-form.js'), 1)
            self.assertEqual(html.count('daily-project-editor.js'), 1)
            self.assertEqual(html.count('task-date-defaults.js'), 1)
            self.assertNotIn('manage-tasks.js', html)
        self.assertNotIn('/static/app.js', daily)
        self.assertNotIn('/static/manage.js', daily)
        self.assertIn('日常项目', vehicle)
        self.assertIn('造车任务', daily)

    def test_create_controls_follow_existing_roles(self):
        vehicle = self.member.get('/tasks').get_data(as_text=True)
        daily = self.member.get('/tasks?type=daily').get_data(as_text=True)
        self.assertNotIn('id="new-task-toggle"', vehicle)
        self.assertNotIn('id="task-create-panel"', vehicle)
        self.assertNotIn('id="daily-new"', daily)
        self.assertNotIn('id="new-task-toggle"', daily)
        self.assertNotIn('id="task-create-panel"', daily)
        self.assertIn('id="new-task-toggle"', self.admin.get('/tasks?type=daily').get_data(as_text=True))

    def test_old_daily_routes_redirect_to_task_center(self):
        self.assertEqual(self.admin.get('/daily-projects').location, '/tasks?type=daily')
        self.assertEqual(self.admin.get('/daily-projects/23').location, '/tasks?type=daily&project=23')

    def test_old_and_new_login_destinations(self):
        destinations = {
            '/daily-projects': '/tasks?type=daily',
            '/daily-projects/123': '/tasks?type=daily&project=123',
            '/tasks?type=daily&project=123': '/tasks?type=daily&project=123',
            '/tasks?type=daily': '/tasks?type=daily',
            '/tasks?task=123': '/tasks?task=123',
            '/tasks?type=vehicle&task=123': '/tasks?type=vehicle&task=123',
        }
        for candidate, expected in destinations.items():
            with self.subTest(candidate=candidate):
                client = app_module.app.test_client()
                result = client.post('/login', json={'username': 'admin', 'password': '123456', 'next': candidate})
                self.assertEqual(result.get_json()['redirect'], expected)

    def test_untrusted_next_is_not_a_redirect(self):
        for candidate in ['//evil.example', 'https://evil.example', '/tasks?type=daily&next=//evil.example',
                          '/tasks?type=daily&project=<script>', '/tasks?type=vehicle&task=1&foo=x',
                          '/tasks?type=daily&project=1#javascript:alert(1)', {'next': '/tasks'}]:
            with self.subTest(candidate=candidate):
                self.assertEqual(app_module.task_login_destination(candidate), '/')

    def test_daily_project_id_does_not_become_markup(self):
        html = self.admin.get('/tasks?type=daily&project=%22%3E%3Cscript%3E').get_data(as_text=True)
        self.assertIn('data-project-id=""', html)


if __name__ == '__main__':
    unittest.main()
