"""归属字段只有管理员/项管可调整；负责人和参与者仍可更新状态。"""
import unittest
from uuid import uuid4

from test_tasks import app_module


class TaskOwnershipAuthorization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = app_module.app.test_client()
        cls.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        cls.clients = {}
        cls.ids = {}
        for role in ('member', 'manager'):
            username = 'ownership_' + uuid4().hex[:10]
            created = cls.admin.post('/api/member', json={
                'name': username, 'username': username,
                'password': 'ownership-password', 'role': role})
            cls.assert_ok(cls, created)
            cls.ids[role] = created.get_json()['id']
            client = app_module.app.test_client()
            login = client.post('/login', json={'username': username, 'password': 'ownership-password'})
            cls.assert_ok(cls, login)
            cls.clients[role] = client
        username = 'owner_' + uuid4().hex[:10]
        created = cls.admin.post('/api/member', json={
            'name': username, 'username': username,
            'password': 'ownership-password', 'role': 'member'})
        cls.assert_ok(cls, created)
        cls.owner_id = created.get_json()['id']

    @staticmethod
    def assert_ok(case, response):
        case.assertTrue(response.get_json().get('ok'), response.get_json())

    def setUp(self):
        created = self.admin.post('/api/task', json={
            'title': '归属权限任务-' + uuid4().hex[:6],
            'owner_id': self.owner_id,
            'participant_ids': [self.ids['member']],
        })
        self.assertEqual(created.status_code, 200, created.get_json())
        self.task_id = created.get_json()['id']
        self.version = created.get_json()['version']

    def update(self, role, **changes):
        payload = {'version': self.version, **changes}
        return self.clients[role].post(f'/api/task/{self.task_id}', json=payload)

    def test_participant_may_update_status(self):
        response = self.update('member', status='进行中')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['version'], self.version + 1)

    def test_participant_may_resend_unchanged_ownership(self):
        response = self.update('member', status='阻塞', owner_id=self.owner_id,
                               participant_ids=[self.ids['member']], blocker_reason='等待设备', blocker_next_action='借用设备')
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_participant_cannot_change_owner(self):
        response = self.update('member', owner_id=self.ids['member'])
        self.assertEqual(response.status_code, 403, response.get_json())
        self.assertIn('负责人', response.get_json()['error'])

    def test_participant_cannot_change_participants_or_vehicle_group(self):
        added = self.update('member', participant_ids=[self.ids['member'], self.owner_id])
        self.assertEqual(added.status_code, 403, added.get_json())
        self.assertIn('参与成员', added.get_json()['error'])
        project = self.admin.post('/api/projects', json={'name': '归属组-' + uuid4().hex[:6]}).get_json()['id']
        moved = self.update('member', project_id=project)
        self.assertEqual(moved.status_code, 403, moved.get_json())
        self.assertIn('造车组别', moved.get_json()['error'])

    def test_manager_may_reassign_owner(self):
        response = self.update('manager', owner_id=self.ids['manager'])
        self.assertEqual(response.status_code, 200, response.get_json())
        detail = self.admin.get('/api/task/%d/logs' % self.task_id)
        self.assertEqual(detail.status_code, 200)


if __name__ == '__main__':
    unittest.main()
