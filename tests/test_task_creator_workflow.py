"""Task creators can maintain plans without acquiring execution privileges."""
import unittest
from uuid import uuid4

from test_tasks import app_module


class CreatorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        self.creator, self.creator_id = self.member()
        self.executor, self.executor_id = self.member()

    def member(self):
        name = 'flow_' + uuid4().hex[:12]
        result = self.admin.post('/api/member', json={
            'username': name, 'name': name, 'password': 'workflow-password', 'role': 'member'})
        client = app_module.app.test_client()
        client.post('/login', json={'username': name, 'password': 'workflow-password'})
        return client, result.get_json()['id']

    def test_vehicle_creator_can_edit_plan_but_cannot_execute_or_reassign(self):
        created = self.creator.post('/api/task', json={
            'title': '计划', 'owner_id': self.executor_id}).get_json()
        path = '/api/task/' + str(created['id'])
        result = self.creator.post(path, json={'version': 1, 'title': '更新计划', 'due_date': '2027-01-10'})
        self.assertEqual(result.status_code, 200, result.get_json())
        for forbidden in ({'progress': 100}, {'status': '已取消'}, {'owner_id': self.creator_id},
                          {'created_by': self.executor_id}, {'priority': '高'}):
            response = self.creator.post(path, json={'version': 2, **forbidden})
            self.assertEqual(response.status_code, 403, response.get_json())
        rows = self.creator.get('/api/data').get_json()['tasks']
        task = next(t for t in rows if t['id'] == created['id'])
        self.assertEqual(task['title'], '更新计划')
        self.assertEqual(task['status'], '未开始')

    def test_daily_creator_edits_plan_and_executor_cancels_and_reopens_only_own_group(self):
        other, other_id = self.member()
        created = self.creator.post('/api/daily-projects', json={
            'title': '分组计划', 'groups': {'mechanical': [self.executor_id], 'vision': [other_id]}}).get_json()
        path = '/api/daily-projects/' + str(created['id'])
        saved = self.creator.post(path, json={'version': 1, 'title': '新计划', 'detail': '用途'})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json()['project']['created_by'], self.creator_id)
        denied = self.creator.post(path, json={'version': 2, 'status': '已完成'})
        self.assertEqual(denied.status_code, 403)
        completed = other.post(path + '/groups/vision/progress', json={
            'version': 1, 'project_version': 2, 'progress': 100, 'note': '视觉完成'})
        self.assertEqual(completed.status_code, 200, completed.get_json())
        project = completed.get_json()['project']
        action = {'version': project['version'], 'action': 'cancel', 'note': '暂时暂停'}
        cancelled = self.executor.post(path, json=action)
        self.assertEqual(cancelled.status_code, 200, cancelled.get_json())
        project = cancelled.get_json()['project']
        denied = self.creator.post(path, json={'version': project['version'], 'title': '不能偷改'})
        self.assertEqual(denied.status_code, 409)
        action = {'version': project['version'], 'action': 'reopen', 'note': '恢复执行',
                  'group_versions': {g['stage']: g['version'] for g in project['groups']},
                  'reopen_progress': {'vision': 20}}
        self.assertEqual(self.executor.post(path, json=action).status_code, 403)
        action['reopen_progress'] = {'mechanical': 20}
        reopened = self.executor.post(path, json=action)
        self.assertEqual(reopened.status_code, 200, reopened.get_json())
        groups = {g['stage']: g['progress'] for g in reopened.get_json()['project']['groups']}
        self.assertEqual(groups['vision'], 100)
        self.assertEqual(groups['mechanical'], 20)

    def test_vehicle_terminal_requires_explicit_reopen_and_reason(self):
        item = self.creator.post('/api/task', json={'title': '终态', 'owner_id': self.executor_id}).get_json()
        path = '/api/task/' + str(item['id'])
        self.assertEqual(self.executor.post(path, json={'version': 1, 'status': '已取消'}).status_code, 400)
        done = self.executor.post(path, json={'version': 1, 'status': '已完成'})
        self.assertEqual(done.status_code, 200)
        self.assertEqual(self.creator.post(path, json={'version': 2, 'title': '终态偷改'}).status_code, 409)
        self.assertEqual(self.executor.post(path + '/progress', data={'version': 2, 'progress': 20, 'note': '绕过重开'}).status_code, 409)
        self.assertEqual(self.executor.post(path, json={'version': 2, 'status': '进行中', 'progress': 20}).status_code, 400)
        reopened = self.executor.post(path, json={'version': 2, 'status': '进行中', 'progress': 20, 'note': '补充验收'})
        self.assertEqual(reopened.status_code, 200, reopened.get_json())

