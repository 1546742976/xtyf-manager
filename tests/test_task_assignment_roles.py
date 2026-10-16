"""Administrators manage work without becoming newly assigned participants."""
import unittest
from uuid import uuid4

from common import get_db
from test_tasks import app_module


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
        return body

    def test_daily_create_rejects_admin_and_rolls_back_whole_request(self):
        with self.app.app_context():
            before = get_db().execute('SELECT count(*) FROM daily_projects').fetchone()[0]
        for groups in ({'mechanical': [self.admin_id]},
                       {'mechanical': [self.member_ids[0]], 'vision': [self.member_ids[1], self.admin_id]}):
            response = self.admin.post('/api/daily-projects', json={'title': '不得保存', 'groups': groups})
            self.assertEqual(response.status_code, 400)
            self.assertIn('改选非管理员负责人', response.get_json()['error'])
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT count(*) FROM daily_projects').fetchone()[0], before)

    def test_daily_update_rejects_mixed_assignment_without_changing_other_fields(self):
        pid = self.create_daily()
        original = self.daily(pid)
        response = self.admin.post(f'/api/daily-projects/{pid}', json={
            **self.daily_body(original), 'title': '不得保存的标题', 'status': '阻塞',
            'groups': {'mechanical': [self.member_ids[0]], 'vision': [self.admin_id]}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.daily(pid), original)
        with self.app.app_context():
            self.assertIsNone(get_db().execute("SELECT 1 FROM daily_groups WHERE project_id=? AND stage='vision'", (pid,)).fetchone())

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

    def test_historical_admin_assignment_is_preserved_until_explicit_reassignment(self):
        pid = self.create_daily()
        self.admin.post(f'/api/daily-projects/{pid}/groups/mechanical/progress', json={
            'progress': 35, 'version': 1, 'project_version': 1, 'note': '历史进度保留'})
        # Simulate a pre-existing relationship without invoking a data migration.
        with self.app.app_context():
            db = get_db()
            db.execute('INSERT INTO daily_owners VALUES(?,?,?)', (pid, 'mechanical', self.admin_id))
            db.commit()
        original = self.daily(pid)
        rejected = self.admin.post(f'/api/daily-projects/{pid}', json={
            **self.daily_body(original), 'title': '保留旧管理员时不能保存'})
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('改选非管理员负责人', rejected.get_json()['error'])
        self.assertEqual(self.daily(pid), original)
        replacement = self.admin.post(f'/api/daily-projects/{pid}', json={
            **self.daily_body(original), 'groups': {'mechanical': [self.member_ids[0]]}})
        self.assertEqual(replacement.status_code, 200)
        changed = self.daily(pid)
        self.assertEqual(changed['owner_ids'], [self.member_ids[0]])
        self.assertEqual(changed['groups'][0]['latest']['note'], '历史进度保留')
        self.assertEqual(changed['groups'][0]['progress'], 35)

    def test_vehicle_admin_owner_and_participant_rejected_atomically(self):
        for fields in ({'owner_id': self.admin_id},
                       {'owner_id': self.member_ids[0], 'participant_ids': [self.admin_id]}):
            response = self.admin.post('/api/task', json={'title': '非法造车分配', **fields})
            self.assertEqual(response.status_code, 400)
        created = self.admin.post('/api/task', json={
            'title': '合法造车任务', 'owner_id': self.member_ids[0], 'participant_ids': [self.member_ids[1]]}).get_json()
        def task():
            return next(item for item in self.admin.get('/api/data').get_json()['tasks'] if item['id'] == created['id'])
        original = task()
        for fields in ({'owner_id': self.admin_id}, {'participant_ids': [*self.member_ids, self.admin_id]}):
            response = self.admin.post(f'/api/task/{created["id"]}', json={
                'version': created['version'], 'title': '非法字段不得部分保存', **fields})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(task(), original)

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
