"""Regression cases found during the independent repair review."""
import unittest
from uuid import uuid4

from test_tasks import app_module
from common import get_db


class DailyRepairReviewTests(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        self.owner = self.admin.get('/api/account/profile').get_json()['profile']['id']

    def create(self, **changes):
        response = self.admin.post('/api/daily-projects', json={
            'title': uuid4().hex, 'status': '进行中',
            'groups': {'mechanical': [self.owner], 'vision': [self.owner]}, **changes})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['id']

    def get(self, pid):
        return self.admin.get(f'/api/daily-projects/{pid}').get_json()['project']

    def save(self, project, **changes):
        body = {key: project[key] for key in ('title', 'detail', 'status', 'priority', 'start_date', 'due_date', 'version')}
        body.update(groups={g['stage']: [o['id'] for o in g['owners']] for g in project['groups'] if g['enabled']},
            group_versions={g['stage']: g['version'] for g in project['groups']}, groups_mode='replace')
        return self.admin.post(f'/api/daily-projects/{project["id"]}', json={**body, **changes})

    def test_cancel_cannot_disable_the_only_unfinished_group_to_keep_100_percent(self):
        pid = self.create()
        project = self.get(pid)
        group = next(g for g in project['groups'] if g['stage'] == 'mechanical')
        response = self.admin.post(f'/api/daily-projects/{pid}/groups/mechanical/progress', json={
            'progress': 100, 'version': group['version'], 'project_version': project['version']})
        self.assertEqual(response.status_code, 200, response.get_json())
        before = self.get(pid)
        self.assertEqual((before['status'], before['progress']), ('进行中', 50))
        rejected = self.save(before, status='已取消', note='结束计划', groups={'mechanical': [self.owner]})
        self.assertEqual(rejected.status_code, 400, rejected.get_json())
        self.assertEqual(self.get(pid), before, 'rejected cancellation must also roll back group disabling')
        accepted = self.save(before, status='已取消', note='结束计划')
        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        self.assertEqual((self.get(pid)['status'], self.get(pid)['progress']), ('已取消', 50))

    def test_archived_existing_helper_is_preserved_but_cannot_be_newly_assigned(self):
        name = 'helper_' + uuid4().hex[:10]
        helper = self.admin.post('/api/member', json={'name': name, 'username': name, 'password': 'helper-password'}).get_json()['id']
        pid = self.create(status='阻塞', blocker_reason='等待设备', blocker_next_action='协调设备', blocker_helper_id=helper)
        with app_module.app.app_context():
            db = get_db(); db.execute('UPDATE members SET is_active=0 WHERE id=?', (helper,)); db.commit()
        before = self.get(pid)
        changed = self.save(before, title='仅标题变化', blocker_reason=before['blocker_reason'],
            blocker_next_action=before['blocker_next_action'], blocker_helper_id=helper, blocker_follow_up=before['blocker_follow_up'])
        self.assertEqual(changed.status_code, 200, changed.get_json())
        self.assertEqual(self.get(pid)['blocker_helper_id'], helper)
        changed = self.admin.post(f'/api/daily-projects/{pid}/blocker', json={
            'version': self.get(pid)['version'], 'blocker_action': 'update',
            'blocker_next_action': '设备已预约，等待交接', 'blocker_helper_id': helper})
        self.assertEqual(changed.status_code, 200, changed.get_json())
        other = self.create()
        denied = self.admin.post(f'/api/daily-projects/{other}/blocker', json={
            'version': self.get(other)['version'], 'blocker_action': 'open',
            'blocker_reason': 'r', 'blocker_next_action': 'n', 'blocker_helper_id': helper})
        self.assertEqual(denied.status_code, 400, denied.get_json())


if __name__ == '__main__':
    unittest.main()
