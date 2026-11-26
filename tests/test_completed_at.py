"""Proven completion timestamps; old completed rows intentionally stay unknown."""
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from test_tasks import app_module
import test_migrations_collaboration as legacy_migrations
from common import get_db
from modules.task_migrations import migrate as migrate_tasks
from modules.daily_schema import migrate as migrate_daily


class CompletedAtTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.admin = cls.app.test_client()
        cls.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        username = 'completed_' + uuid4().hex[:12]
        cls.member_id = cls.admin.post('/api/member', json={
            'name': username, 'username': username, 'password': 'completed-test-password',
        }).get_json()['id']

    def task(self, **body):
        result = self.admin.post('/api/task', json={'title': '完成时间测试', 'owner_id': self.member_id, **body})
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()['id']

    def read_task(self, task_id):
        return next(row for row in self.admin.get('/api/data').get_json()['tasks'] if row['id'] == task_id)

    def change_task(self, task_id, **changes):
        task = self.read_task(task_id)
        response = self.admin.post(f'/api/task/{task_id}', json={'version': task['version'], **changes})
        self.assertEqual(response.status_code, 200, response.get_json())
        return self.read_task(task_id)

    def project(self, **body):
        result = self.admin.post('/api/daily-projects', json={
            'title': '日常完成时间', 'groups': {'mechanical': [self.member_id]}, **body,
        })
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()['id']

    def read_project(self, pid):
        return self.admin.get(f'/api/daily-projects/{pid}').get_json()['project']

    def change_project(self, pid, **changes):
        project = self.read_project(pid)
        body = {key: project[key] for key in ('title', 'detail', 'status', 'priority', 'start_date', 'due_date', 'version')}
        body['groups'] = {group['stage']: [owner['id'] for owner in group['owners']] for group in project['groups'] if group['enabled']}
        body['group_versions'] = {group['stage']: group['version'] for group in project['groups']}
        return self.admin.post(f'/api/daily-projects/{pid}', json={**body, **changes})

    def test_beijing_completion_day_is_independent_of_utc_host_clock(self):
        instant = datetime(2026, 10, 16, 16, 1, 2, tzinfo=timezone.utc)

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

        with patch('modules.task_mutations.datetime', FixedClock), patch('modules.daily_projects.datetime', FixedClock):
            task_id = self.task(status='已完成', completed_at='1999-01-01 00:00:00')
            pid = self.project(status='已完成', completed_at='1999-01-01 00:00:00')
        self.assertEqual(self.read_task(task_id)['completed_at'], '2026-10-17 00:01:02')
        self.assertEqual(self.read_project(pid)['completed_at'], '2026-10-17 00:01:02')

    def test_task_status_transitions_and_unrelated_updates(self):
        task_id = self.task()
        self.assertIsNone(self.read_task(task_id)['completed_at'])
        with patch('modules.task_mutations.completion_stamp', return_value='2026-10-16 21:15:00'):
            completed = self.change_task(task_id, status='已完成')
        self.assertEqual(completed['completed_at'], '2026-10-16 21:15:00')
        with patch('modules.task_mutations.completion_stamp', return_value='2026-10-17 21:15:00') as clock:
            edited = self.change_task(task_id, correction=True, note='核实名称纠错', title='完成后更名', due_date='2026-12-01', status='已完成')
            self.assertEqual(edited['completed_at'], completed['completed_at'])
            clock.assert_not_called()
        # Terminal progress remains read-only and retains the original completion time.
        response = self.admin.post(f'/api/task/{task_id}/progress', data={'version': edited['version'], 'progress': '100', 'note': '补充验收记录'})
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(self.read_task(task_id)['completed_at'], completed['completed_at'])
        self.assertIsNone(self.change_task(task_id, status='进行中', progress=60, note='需要继续执行')['completed_at'])
        with patch('modules.task_mutations.completion_stamp', return_value='2026-10-18 08:00:00'):
            again = self.change_task(task_id, status='已完成')
        self.assertEqual(again['completed_at'], '2026-10-18 08:00:00')
        self.change_task(task_id, status='进行中', progress=60, note='需要继续执行')
        self.assertIsNone(self.change_task(task_id, status='已取消', note='计划取消')['completed_at'])

    def test_task_undo_restores_original_completion_timestamp(self):
        with patch('modules.task_mutations.completion_stamp', return_value='2026-10-16 20:00:00'):
            task_id = self.task(status='已完成')
        opened = self.change_task(task_id, status='进行中', progress=60, note='需要继续执行')
        endpoint = f'/api/task/{task_id}'
        revisions = self.admin.get(endpoint + '/revisions').get_json()['revisions']
        reopened_revision = revisions[0]
        self.assertEqual(reopened_revision['before']['completed_at'], '2026-10-16 20:00:00')
        result = self.admin.post(endpoint + f"/revisions/{reopened_revision['id']}/revert", json={'version': opened['version']})
        self.assertEqual(result.status_code, 200, result.get_json())
        restored = self.read_task(task_id)
        self.assertEqual((restored['status'], restored['completed_at']), ('已完成', '2026-10-16 20:00:00'))
        undo = self.admin.get(endpoint + '/revisions').get_json()['revisions'][0]
        self.assertEqual(undo['before']['completed_at'], None)
        self.assertEqual(undo['after']['completed_at'], '2026-10-16 20:00:00')

    def test_completion_revert_preserves_unrelated_changes_and_conflict_is_atomic(self):
        task_id = self.task()
        with patch('modules.task_mutations.completion_stamp', return_value='2026-10-16 20:00:00'):
            self.change_task(task_id, status='已完成')
        endpoint = f'/api/task/{task_id}'
        revision = self.admin.get(endpoint + '/revisions').get_json()['revisions'][0]
        current = self.change_task(task_id, correction=True, note='核实名称纠错', title='完成后更名')
        stale = self.admin.post(endpoint + f"/revisions/{revision['id']}/revert", json={'version': current['version'] - 1})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.read_task(task_id)['completed_at'], '2026-10-16 20:00:00')
        success = self.admin.post(endpoint + f"/revisions/{revision['id']}/revert", json={'version': current['version']})
        self.assertEqual(success.status_code, 200, success.get_json())
        task = self.read_task(task_id)
        self.assertEqual((task['title'], task['status'], task['completed_at']), ('完成后更名', '未开始', None))

    def test_legacy_status_revision_cannot_invent_a_completion_time(self):
        task_id = self.task()
        with self.app.app_context():
            db = get_db()
            revision = db.execute(
                'INSERT INTO task_revisions(task_id,actor_id,actor_name,created_at,before_json,after_json,version,kind) VALUES(?,?,?,?,?,?,?,?)',
                (task_id, 1, '管理员', '2026-10-15 12:00:00', json.dumps({'status': '已完成'}), json.dumps({'status': '未开始'}), 1, 'update'),
            ).lastrowid
            db.commit()
        result = self.admin.post(f'/api/task/{task_id}/revisions/{revision}/revert', json={'version': 1})
        self.assertEqual(result.status_code, 409, result.get_json())
        task = self.read_task(task_id)
        self.assertEqual(task['status'], '未开始')
        self.assertEqual(task['progress'], 0)
        self.assertIsNone(task['completed_at'])

    def test_daily_complete_reopen_recomplete_and_terminal_readonly(self):
        pid = self.project()
        self.assertIsNone(self.read_project(pid)['completed_at'])
        with patch('modules.daily_projects.stamp', return_value='2026-10-16 20:00:00'):
            self.assertEqual(self.change_project(pid, status='已完成').status_code, 200)
        current = self.read_project(pid)
        self.assertEqual(current['completed_at'], '2026-10-16 20:00:00')
        with patch('modules.daily_projects.stamp', return_value='2026-10-17 20:00:00'):
            self.assertEqual(self.change_project(pid, title='终态仍只读').status_code, 409)
        self.assertEqual(self.read_project(pid), current)
        self.assertEqual(self.change_project(pid, status='进行中', reopen_progress={'mechanical': 60}, note='需要继续执行').status_code, 200)
        self.assertIsNone(self.read_project(pid)['completed_at'])
        with patch('modules.daily_projects.stamp', return_value='2026-10-18 20:00:00'):
            self.assertEqual(self.change_project(pid, status='已完成').status_code, 200)
        self.assertEqual(self.read_project(pid)['completed_at'], '2026-10-18 20:00:00')
        self.assertEqual(self.change_project(pid, status='进行中', reopen_progress={'mechanical': 60}, note='需要继续执行').status_code, 200)
        self.assertEqual(self.change_project(pid, status='已取消', note='计划取消').status_code, 200)
        self.assertIsNone(self.read_project(pid)['completed_at'])

    def test_repeat_migrations_preserve_proven_dates_and_leave_legacy_completed_null(self):
        db = legacy_migrations.TestCollaborationMigration()._legacy_db()
        try:
            db.execute("UPDATE tasks SET status='已完成',updated_at='2026-10-16 20:00:00'")
            db.execute("""CREATE TABLE daily_projects (
                id INTEGER PRIMARY KEY,title TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '未开始',priority TEXT NOT NULL DEFAULT '中',
                start_date TEXT,due_date TEXT,version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT)""")
            db.execute("INSERT INTO daily_projects(id,title,status,created_at,updated_at) VALUES(1,'历史完成','已完成','old','2026-10-16 20:00:00')")
            db.commit()
            for _ in range(2):
                migrate_tasks(db); migrate_daily(db)
            self.assertIsNone(db.execute('SELECT completed_at FROM tasks WHERE id=1').fetchone()[0])
            self.assertIsNone(db.execute('SELECT completed_at FROM daily_projects WHERE id=1').fetchone()[0])
            for table in ('tasks', 'daily_projects'):
                db.execute(f"UPDATE {table} SET completed_at='2026-10-17 08:00:00' WHERE id=1")
            db.commit()
            migrate_tasks(db); migrate_daily(db)
            for table in ('tasks', 'daily_projects'):
                self.assertEqual(db.execute(f'SELECT completed_at FROM {table} WHERE id=1').fetchone()[0], '2026-10-17 08:00:00')
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
