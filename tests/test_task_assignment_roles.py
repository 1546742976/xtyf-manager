"""Business ownership is independent of permission roles, but requires active accounts."""
import unittest
from uuid import uuid4

# 先导入测试夹具（设置临时库/目录），再导入项目模块。
from test_tasks import app_module
from common import get_db


class TaskAssignmentRoles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.admin = cls.app.test_client()
        cls.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        cls.admin_id = cls.admin.get('/api/account/profile').get_json()['profile']['id']
        cls.member_ids = []
        for role in ('member', 'manager'):
            username = 'assignment_' + uuid4().hex[:10]
            response = cls.admin.post('/api/member', json={
                'name': '管理员' if role == 'member' else '项管负责人', 'username': username,
                'password': 'assignment-test-password', 'role': role})
            cls.member_ids.append(response.get_json()['id'])

    def create_daily(self):
        response = self.admin.post('/api/daily-projects', json={
            'title': '角色分配测试', 'groups': {'mechanical': self.member_ids}})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['id']

    def daily(self, pid):
        return self.admin.get(f'/api/daily-projects/{pid}').get_json()['project']

    def daily_body(self, project):
        body = {key: project[key] for key in ('title', 'detail', 'status', 'priority', 'start_date', 'due_date', 'version')}
        body['groups'] = {group['stage']: [owner['id'] for owner in group['owners']]
                          for group in project['groups'] if group['enabled']}
        body['group_versions'] = {group['stage']: group['version'] for group in project['groups']}
        return body

    def test_daily_create_accepts_admin_as_owner_across_groups(self):
        response = self.admin.post('/api/daily-projects', json={
            'title': '管理员参与的日常目标',
            'groups': {'mechanical': [self.admin_id],
                       'vision': [self.member_ids[0], self.admin_id]}})
        self.assertEqual(response.status_code, 200, response.get_json())
        project = self.daily(response.get_json()['id'])
        self.assertEqual(project['owner_ids'], sorted([self.admin_id, self.member_ids[0]]))
        groups = {group['stage']: [owner['id'] for owner in group['owners']] for group in project['groups']}
        self.assertEqual(groups['mechanical'], [self.admin_id])
        self.assertEqual(groups['vision'], sorted([self.admin_id, self.member_ids[0]]))

    def test_daily_update_accepts_admin_and_keeps_other_changes(self):
        pid = self.create_daily()
        original = self.daily(pid)
        response = self.admin.post(f'/api/daily-projects/{pid}', json={
            **self.daily_body(original), 'title': '管理员直接负责',
            'status': '阻塞', 'blocker_reason': '等待联调设备', 'blocker_next_action': '协调设备',
            'groups': {'mechanical': [self.member_ids[0]],
                                          'vision': [self.admin_id]}})
        self.assertEqual(response.status_code, 200, response.get_json())
        updated = self.daily(pid)
        self.assertEqual(updated['title'], '管理员直接负责')
        self.assertEqual(updated['status'], '阻塞')
        self.assertEqual(updated['owner_ids'], sorted([self.member_ids[0], self.admin_id]))
        self.assertEqual([owner['id'] for group in updated['groups'] if group['stage'] == 'vision'
                          for owner in group['owners']], [self.admin_id])

    def test_nonadmin_roles_assignable_and_admin_can_manage_and_update_progress(self):
        pid = self.create_daily()
        self.assertEqual(self.daily(pid)['owner_ids'], self.member_ids)
        response = self.admin.post(f'/api/daily-projects/{pid}/groups/mechanical/progress', json={
            'progress': 70, 'version': 1, 'project_version': 1, 'note': '管理员代记录进度'})
        self.assertEqual(response.status_code, 200)
        original = self.daily(pid)
        response = self.admin.post(f'/api/daily-projects/{pid}', json={
            **self.daily_body(original), 'title': '管理员可正常管理'})
        self.assertEqual(response.status_code, 200)
        project = self.daily(pid)
        self.assertEqual(project['title'], '管理员可正常管理')
        self.assertEqual(project['groups'][0]['progress'], 70)
        self.assertEqual(project['groups'][0]['latest']['actor_name'], '管理员')

    def test_existing_admin_assignment_can_be_edited_without_reassignment(self):
        pid = self.create_daily()
        self.admin.post(f'/api/daily-projects/{pid}/groups/mechanical/progress', json={
            'progress': 35, 'version': 1, 'project_version': 1, 'note': '原有进度保留'})
        with self.app.app_context():
            db = get_db()
            db.execute('INSERT INTO daily_owners VALUES(?,?,?)', (pid, 'mechanical', self.admin_id))
            db.commit()
        original = self.daily(pid)
        response = self.admin.post(f'/api/daily-projects/{pid}', json={
            **self.daily_body(original), 'title': '管理员分配可以延续'})
        self.assertEqual(response.status_code, 200, response.get_json())
        updated = self.daily(pid)
        self.assertIn(self.admin_id, updated['owner_ids'])
        self.assertEqual(updated['title'], '管理员分配可以延续')
        self.assertEqual(updated['groups'][0]['latest']['note'], '原有进度保留')
        self.assertEqual(updated['groups'][0]['progress'], 35)

    def test_vehicle_accepts_admin_as_owner_and_participant(self):
        first = self.admin.post('/api/task', json={
            'title': '管理员担任造车负责人', 'owner_id': self.admin_id,
            'participant_ids': [self.member_ids[0]]})
        self.assertEqual(first.status_code, 200, first.get_json())
        second = self.admin.post('/api/task', json={
            'title': '管理员担任造车参与者', 'owner_id': self.member_ids[0],
            'participant_ids': [self.admin_id]})
        self.assertEqual(second.status_code, 200, second.get_json())

        def task(task_id):
            return next(item for item in self.admin.get('/api/data').get_json()['tasks']
                        if item['id'] == task_id)

        created = task(first.get_json()['id'])
        self.assertEqual(created['owner_id'], self.admin_id)
        self.assertIn(self.member_ids[0], created['participant_ids'])
        updated = self.admin.post(f'/api/task/{second.get_json()["id"]}', json={
            'version': second.get_json()['version'], 'owner_id': self.admin_id,
            'participant_ids': [self.member_ids[0], self.admin_id]})
        self.assertEqual(updated.status_code, 200, updated.get_json())
        saved = task(second.get_json()['id'])
        self.assertEqual(saved['owner_id'], self.admin_id)
        self.assertEqual(sorted(saved['participant_ids']), sorted([self.admin_id, self.member_ids[0]]))

    def test_inactive_member_cannot_be_newly_assigned_regardless_of_role(self):
        name = 'inactive_assignment_' + uuid4().hex[:10]
        member = self.admin.post('/api/member', json={
            'name': name, 'username': name, 'password': 'assignment-test-password',
            'role': 'manager'}).get_json()
        with self.app.app_context():
            db = get_db()
            db.execute('UPDATE members SET is_active=0 WHERE id=?', (member['id'],))
            db.commit()
        daily = self.admin.post('/api/daily-projects', json={
            'title': '停用成员不可担任负责人', 'groups': {'mechanical': [member['id']]}})
        self.assertEqual(daily.status_code, 400)
        vehicle = self.admin.post('/api/task', json={
            'title': '停用成员不可担任负责人', 'owner_id': member['id']})
        self.assertEqual(vehicle.status_code, 400)

    def test_vehicle_historical_admin_relationship_and_log_are_not_rewritten(self):
        created = self.admin.post('/api/task', json={'title': '历史造车任务', 'owner_id': self.member_ids[0]}).get_json()
        with self.app.app_context():
            db = get_db()
            db.execute('INSERT INTO task_participants VALUES(?,?)', (created['id'], self.admin_id))
            db.execute('INSERT INTO logs(task_id,member_id,member_name,note,changes,created_at) VALUES(?,?,?,?,?,?)',
                       (created['id'], self.admin_id, '管理员', '历史备注', '', '2026-10-16 08:00:00'))
            db.commit()
        response = self.admin.post(f'/api/task/{created["id"]}', json={
            'version': created['version'], 'title': '仅修改标题'})
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            db = get_db()
            self.assertIsNotNone(db.execute('SELECT 1 FROM task_participants WHERE task_id=? AND member_id=?',
                                           (created['id'], self.admin_id)).fetchone())
            self.assertEqual(db.execute("SELECT note FROM logs WHERE task_id=? AND note='历史备注'",
                                        (created['id'],)).fetchone()[0], '历史备注')


if __name__ == '__main__':
    unittest.main()
