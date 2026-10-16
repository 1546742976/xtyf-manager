"""Vehicle-stage scheduling through authenticated HTTP and real SQLite transactions."""
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_tasks import app_module
from common import get_db
from modules import task_schedule


class TestTaskSchedule(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.assertTrue(self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'}).get_json()['ok'])
        self.marker = uuid4().hex[:12]
        self.project = self.admin.post('/api/projects', json={'name': '排期' + self.marker}).get_json()['id']
        self.groups = {row['name']: row['id'] for row in self.admin.get('/api/groups').get_json()['groups']}
        self.mechanical = self.member('机械')
        self.electrical = self.member('电控')
        self.vision = self.member('视觉')

    def member(self, *groups, role='member'):
        username = 'sched_' + uuid4().hex[:12]
        result = self.admin.post('/api/member', json={'name': username, 'username': username,
            'password': 'schedule-member-password', 'group_ids': [self.groups[name] for name in groups], 'role': role})
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()['id']

    def task(self, owner, start='2026-10-14', end='2026-10-22', **extra):
        result = self.admin.post('/api/task', json={'title': '排期任务' + uuid4().hex[:6],
            'owner_id': owner, 'project_id': self.project, 'start_date': start, 'due_date': end, **extra})
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()['id']

    def preview(self, choices=None):
        response = self.admin.post(f'/api/projects/{self.project}/schedule/preview', json={'phase_choices': choices or {}})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def apply(self, preview):
        return self.admin.post(f'/api/projects/{self.project}/schedule/apply', json={'snapshot': preview['snapshot']})

    def tasks(self):
        return {row['id']: row for row in self.admin.get('/api/data').get_json()['tasks']}

    def test_parallel_phases_use_latest_end_and_preserve_durations(self):
        m1 = self.task(self.mechanical, end='2026-10-20')
        m2 = self.task(self.mechanical, end='2026-10-22')
        e1 = self.task(self.electrical, '2026-10-15', '2026-10-17', status='进行中', participant_ids=[self.mechanical])
        e2 = self.task(self.electrical, '2026-10-15', '2026-10-19', status='阻塞')
        v = self.task(self.vision, '2026-10-20', '2026-10-23')
        before = self.tasks()
        preview = self.preview()
        self.assertTrue(preview['can_apply'])
        self.assertEqual(preview['changed_count'], 3)
        self.assertEqual(self.tasks()[e1]['start_date'], before[e1]['start_date'], 'preview must not write dates')
        self.assertEqual(self.apply(preview).get_json()['changed_count'], 3)
        after = self.tasks()
        for tid, start, end in [(m1, '2026-10-14', '2026-10-20'), (m2, '2026-10-14', '2026-10-22'),
                               (e1, '2026-10-22', '2026-10-24'), (e2, '2026-10-22', '2026-10-26'),
                               (v, '2026-10-26', '2026-10-29')]:
            self.assertEqual((after[tid]['start_date'], after[tid]['due_date']), (start, end))
        self.assertEqual(after[m1]['version'], 1)
        self.assertEqual(after[e1]['version'], 2)
        self.assertEqual(after[e1]['owner_stage'], '电控')
        self.assertEqual(before[e1]['start_date_origin'], 'manual')
        self.assertEqual(after[e1]['start_date_origin'], 'schedule')
        revision = self.admin.get(f'/api/task/{e1}/revisions').get_json()['revisions'][0]
        self.assertEqual(revision['before']['start_date_origin'], 'manual')
        reverted = self.admin.post(f"/api/task/{e1}/revisions/{revision['id']}/revert", json={'version': after[e1]['version']})
        self.assertEqual(reverted.status_code, 200)
        restored = self.tasks()[e1]
        self.assertEqual((restored['start_date'], restored['start_date_origin']), ('2026-10-15', 'manual'))

    def test_missing_middle_phase_and_zero_day_task(self):
        self.task(self.mechanical)
        v = self.task(self.vision, '2026-10-15', '2026-10-15')
        self.assertEqual(self.apply(self.preview()).status_code, 200)
        task = self.tasks()[v]
        self.assertEqual((task['start_date'], task['due_date']), ('2026-10-22', '2026-10-22'))

    def test_first_existing_phase_stays_and_noop_does_not_increment_version(self):
        e = self.task(self.electrical)
        preview = self.preview()
        self.assertTrue(preview['can_apply'])
        self.assertEqual(preview['changed_count'], 0)
        self.assertEqual(self.apply(preview).get_json()['changed_count'], 0)
        self.assertEqual(self.tasks()[e]['version'], 1)

    def test_excluded_and_unclassified_tasks_and_other_projects_are_untouched(self):
        self.task(self.mechanical)
        e = self.task(self.electrical, '2026-10-15', '2026-10-17')
        completed = self.task(self.mechanical, end='2026-12-31', status='已完成')
        cancelled = self.task(self.mechanical, end='2026-12-31', status='已取消')
        other = self.task(self.member('硬件'), '', '')
        unassigned = self.task(None, '', '')
        deleted = self.task(self.mechanical, end='2026-12-31')
        self.assertTrue(self.admin.post(f'/api/task/{deleted}/delete', json={'version': 1}).get_json()['ok'])
        second = self.admin.post('/api/projects', json={'name': '另一车组' + self.marker}).get_json()['id']
        external = self.task(self.vision, project_id=second)
        before = self.tasks()
        preview = self.preview()
        self.assertNotIn(deleted, [row['id'] for row in preview['rows']])
        self.assertEqual(self.apply(preview).status_code, 200)
        after = self.tasks()
        for tid in (completed, cancelled, other, unassigned, external):
            self.assertEqual(after[tid], before[tid])
        self.assertEqual(after[e]['start_date'], '2026-10-22')

    def test_multi_specialty_selection_is_required_persisted_and_revertible(self):
        self.task(self.mechanical)
        owner = self.member('机械', '视觉')
        tid = self.task(owner, '2026-10-14', '2026-10-16')
        self.assertFalse(self.preview()['can_apply'])
        self.assertEqual(self.apply(self.preview()).status_code, 400)
        preview = self.preview({str(tid): '视觉'})
        self.assertTrue(preview['can_apply'])
        self.assertEqual(self.apply(preview).status_code, 200)
        task = self.tasks()[tid]
        self.assertEqual((task['schedule_stage'], task['owner_stage'], task['start_date']), ('视觉', '视觉', '2026-10-22'))
        self.assertTrue(self.preview()['can_apply'])
        revision = self.admin.get(f'/api/task/{tid}/revisions').get_json()['revisions'][0]
        self.assertEqual(revision['after']['schedule_stage'], '视觉')
        reverted = self.admin.post(f"/api/task/{tid}/revisions/{revision['id']}/revert", json={'version': task['version']})
        self.assertEqual(reverted.status_code, 200, reverted.get_json())
        restored = self.tasks()[tid]
        self.assertIsNone(restored['schedule_stage'])
        self.assertEqual(restored['start_date'], '2026-10-14')

    def test_incompatible_saved_stage_requires_new_choice(self):
        owner = self.member('机械', '视觉')
        tid = self.task(owner)
        self.assertEqual(self.apply(self.preview({str(tid): '视觉'})).status_code, 200)
        changed = self.admin.post(f'/api/member/{owner}/groups', json={'group_ids': [self.groups['机械'], self.groups['电控']]})
        self.assertEqual(changed.status_code, 200)
        self.assertIsNone(self.tasks()[tid]['owner_stage'])
        self.assertFalse(self.preview()['can_apply'])

    def test_missing_invalid_and_overflow_dates_block_entire_plan(self):
        self.task(self.mechanical)
        e = self.task(self.electrical, '', '2026-10-17')
        self.assertFalse(self.preview()['can_apply'])
        for start, end in [('bad', '2026-10-17'), ('2026-10-18', '2026-10-17')]:
            with app_module.app.app_context():
                db = get_db()
                db.execute('UPDATE tasks SET start_date=?,due_date=? WHERE id=?', (start, end, e))
                db.commit()
            self.assertFalse(self.preview()['can_apply'])
        with app_module.app.app_context():
            db = get_db()
            db.execute('UPDATE tasks SET start_date=?,due_date=? WHERE project_id=? AND owner_id=?',
                       ('9999-12-31', '9999-12-31', self.project, self.mechanical))
            db.execute('UPDATE tasks SET start_date=?,due_date=? WHERE id=?', ('2026-10-14', '2026-10-15', e))
            db.commit()
        self.assertFalse(self.preview()['can_apply'])
        self.assertEqual(self.tasks()[e]['version'], 1)

    def test_task_edit_creation_deletion_and_membership_changes_invalidate_snapshot(self):
        m = self.task(self.mechanical)
        e = self.task(self.electrical, '2026-10-15', '2026-10-17')
        preview = self.preview()
        self.assertTrue(self.admin.post(f'/api/task/{m}', json={'title': '预览后更名', 'version': 1}).get_json()['ok'])
        self.assertEqual(self.apply(preview).status_code, 409)
        preview = self.preview()
        added = self.task(self.mechanical)
        self.assertEqual(self.apply(preview).status_code, 409)
        preview = self.preview()
        self.admin.post(f'/api/task/{added}/delete', json={'version': 1})
        self.assertEqual(self.apply(preview).status_code, 409)
        preview = self.preview()
        self.admin.post(f'/api/member/{self.electrical}/groups', json={'group_ids': [self.groups['视觉']]})
        self.assertEqual(self.apply(preview).status_code, 409)
        self.assertEqual(self.tasks()[e]['start_date'], '2026-10-15')

    def test_catalog_rename_is_detected_without_task_version_change(self):
        self.task(self.mechanical)
        self.task(self.electrical, '2026-10-15', '2026-10-17')
        preview = self.preview()
        gid = self.groups['电控']
        try:
            self.admin.post(f'/api/groups/{gid}', json={'name': '电控更名' + self.marker})
            self.assertEqual(self.apply(preview).status_code, 409)
        finally:
            self.admin.post(f'/api/groups/{gid}', json={'name': '电控'})

    def test_permissions_csrf_and_signed_preview_validation(self):
        self.task(self.mechanical)
        self.task(self.electrical, '2026-10-15', '2026-10-17')
        preview = self.preview()
        member = app_module.app.test_client()
        with app_module.app.app_context():
            username = get_db().execute('SELECT username FROM members WHERE id=?', (self.vision,)).fetchone()[0]
        self.assertTrue(member.post('/login', json={'username': username, 'password': 'schedule-member-password'}).get_json()['ok'])
        for action in ('preview', 'apply'):
            endpoint = f'/api/projects/{self.project}/schedule/{action}'
            self.assertEqual(member.post(endpoint, json={}).status_code, 403)
            self.assertEqual(app_module.app.test_client().post(endpoint, json={}).status_code, 401)
        endpoint = f'/api/projects/{self.project}/schedule/apply'
        self.assertEqual(self.admin.post(endpoint, json={'snapshot': preview['snapshot']}, headers={'X-CSRF-Token': 'invalid'}).status_code, 403)
        self.assertEqual(self.admin.post(endpoint, json={'snapshot': preview['snapshot'] + '!'}).status_code, 400)
        other = self.admin.post('/api/projects', json={'name': 'token车组' + self.marker}).get_json()['id']
        self.assertEqual(self.admin.post(f'/api/projects/{other}/schedule/apply', json={'snapshot': preview['snapshot']}).status_code, 400)
        manager_id = self.member(role='manager')
        with app_module.app.app_context():
            manager_name = get_db().execute('SELECT username FROM members WHERE id=?', (manager_id,)).fetchone()[0]
        manager = app_module.app.test_client()
        manager.post('/login', json={'username': manager_name, 'password': 'schedule-member-password'})
        self.assertEqual(manager.post(f'/api/projects/{self.project}/schedule/preview', json={}).status_code, 200)
        self.assertEqual(manager.post(endpoint, json={'snapshot': preview['snapshot']}).status_code, 200)

    def test_invalid_phase_choices_and_missing_or_archived_project(self):
        tid = self.task(self.member('机械', '视觉'))
        endpoint = f'/api/projects/{self.project}/schedule/preview'
        for choices in ([], None, {'bad': '机械'}, {str(tid): '电控'}, {'999999': '机械'}):
            self.assertEqual(self.admin.post(endpoint, json={'phase_choices': choices}).status_code, 400)
        self.assertEqual(self.admin.post('/api/projects/999999/schedule/preview', json={}).status_code, 404)
        archived = self.admin.post('/api/projects', json={'name': '已归档排期' + self.marker}).get_json()['id']
        self.assertEqual(self.admin.post(f'/api/projects/{archived}/delete', json={'confirm_name': '已归档排期' + self.marker}).status_code, 200)
        self.assertEqual(self.admin.post(f'/api/projects/{archived}/schedule/preview', json={}).status_code, 404)

    def test_batch_failure_rolls_back_dates_versions_revisions_and_logs(self):
        self.task(self.mechanical)
        e1 = self.task(self.electrical, '2026-10-15', '2026-10-17')
        e2 = self.task(self.electrical, '2026-10-15', '2026-10-18')
        preview = self.preview()
        before = self.tasks()
        original_revision = task_schedule._revision
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.IntegrityError('injected second-row failure')
            return original_revision(*args, **kwargs)
        with app_module.app.app_context():
            db = get_db()
            counts = [db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('task_revisions', 'logs')]
            with patch.object(task_schedule, '_revision', side_effect=fail_second):
                with self.assertRaises(sqlite3.IntegrityError):
                    task_schedule.apply_schedule(db, {'id': 1, 'name': '管理员', 'role': 'admin'}, self.project, preview['snapshot'])
            self.assertEqual([db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('task_revisions', 'logs')], counts)
        self.assertEqual(calls, 2)
        for tid in (e1, e2):
            self.assertEqual(self.tasks()[tid], before[tid])


if __name__ == '__main__':
    unittest.main()
