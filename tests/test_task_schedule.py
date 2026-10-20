"""Dependency scheduling through authenticated HTTP and real temporary SQLite."""
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from itsdangerous import URLSafeSerializer
from test_tasks import app_module
from common import get_db
from modules import task_schedule


class TestTaskSchedule(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.assertTrue(self.admin.post('/login', json={'username': 'admin', 'password': '123456'}).get_json()['ok'])
        self.marker = uuid4().hex[:12]
        self.project = self.admin.post('/api/projects', json={'name': '排期' + self.marker}).get_json()['id']
        self.groups = {row['name']: row['id'] for row in self.admin.get('/api/groups').get_json()['groups']}
        self.mechanical = self.member('机械')
        self.electrical = self.member('电控')
        self.vision = self.member('视觉')

    def member(self, *groups, role='member'):
        username = 'sched_' + uuid4().hex[:12]
        response = self.admin.post('/api/member', json={'name': username, 'username': username,
            'password': 'schedule-member-password', 'group_ids': [self.groups[name] for name in groups], 'role': role})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['id']

    def task(self, owner=None, start='2026-10-14', end='2026-10-22', **extra):
        response = self.admin.post('/api/task', json={'title': '排期任务' + uuid4().hex[:6],
            'owner_id': owner, 'project_id': self.project, 'start_date': start, 'due_date': end, **extra})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['id']

    def preview(self, **parameters):
        response = self.admin.post(f'/api/projects/{self.project}/schedule/preview',
                                   json={'algorithm_version': 2, 'base_date': '2026-10-14', **parameters})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def apply(self, preview):
        return self.admin.post(f'/api/projects/{self.project}/schedule/apply', json={'snapshot': preview['snapshot']})

    def tasks(self):
        return {row['id']: row for row in self.admin.get('/api/data').get_json()['tasks']}

    def rows(self, preview):
        return {row['id']: row for row in preview['rows']}

    def sql(self, statement, parameters=()):
        with app_module.app.app_context():
            db = get_db()
            db.execute(statement, parameters)
            db.commit()

    def test_dependency_order_merged_predecessors_and_preserved_inclusive_durations(self):
        # A visual task can precede mechanical: specialties are display only.
        visual = self.task(self.vision, end='2026-10-20')
        electrical = self.task(self.electrical, end='2026-10-24')
        mechanical = self.task(self.mechanical, '2026-10-15', '2026-10-17',
                               parent_id=visual, predecessor_ids=[visual, electrical])
        downstream = self.task(self.vision, '2026-10-16', '2026-10-16', parent_id=mechanical)
        before = self.tasks()
        preview = self.preview(base_date='2026-10-22', task_ids=[mechanical, downstream])
        rows = self.rows(preview)
        self.assertTrue(preview['can_apply'], preview)
        self.assertEqual(rows[mechanical]['duration_days'], 3)
        self.assertEqual(len(rows[mechanical]['predecessors']), 2)
        self.assertEqual(rows[mechanical]['after'], {'start_date': '2026-10-22', 'due_date': '2026-10-24'})
        self.assertEqual(rows[downstream]['after'], {'start_date': '2026-10-22', 'due_date': '2026-10-22'})
        self.assertEqual(self.tasks()[mechanical], before[mechanical], 'preview cannot write')
        self.assertEqual(self.apply(preview).get_json()['changed_count'], 2)
        after = self.tasks()
        self.assertEqual(after[mechanical]['version'], 2)
        self.assertEqual(after[mechanical]['start_date_origin'], 'schedule')
        self.assertEqual(after[visual]['version'], 1)
        revision = self.admin.get(f'/api/task/{mechanical}/revisions').get_json()['revisions'][0]
        self.assertEqual(revision['before']['start_date_origin'], 'manual')
        reverted = self.admin.post(f"/api/task/{mechanical}/revisions/{revision['id']}/revert", json={'version': 2})
        self.assertEqual(reverted.status_code, 200, reverted.get_json())
        restored = self.tasks()[mechanical]
        self.assertEqual((restored['start_date'], restored['start_date_origin']), ('2026-10-15', 'manual'))

    def test_short_predecessor_clamps_boundary_to_start_and_same_day_is_one_day(self):
        predecessor = self.task(self.mechanical, '2026-10-20', '2026-10-20')
        successor = self.task(self.electrical, '2026-10-15', '2026-10-15', parent_id=predecessor)
        row = self.rows(self.preview())[successor]
        self.assertEqual(row['duration_days'], 1)
        self.assertEqual(row['after'], {'start_date': '2026-10-15', 'due_date': '2026-10-15'})
        self.assertTrue(any(item.get('kind') == 'dependency_early_reservation' for item in self.preview()['warnings']))

    def test_base_date_defaults_beijing_and_never_advances_existing_dates(self):
        past = self.task(self.mechanical, '2026-10-14', '2026-10-16')
        future = self.task(self.electrical, '2026-10-25', '2026-10-27')
        rows = self.rows(self.preview(base_date='2026-10-20'))
        self.assertEqual(rows[past]['after'], {'start_date': '2026-10-20', 'due_date': '2026-10-22'})
        self.assertEqual(rows[future]['before'], rows[future]['after'])
        response = self.admin.post(f'/api/projects/{self.project}/schedule/preview', json={'algorithm_version': 2})
        self.assertEqual(response.get_json()['base_date'], datetime.now(timezone(timedelta(hours=8))).date().isoformat())

    def test_noop_does_not_increment_version(self):
        tid = self.task(self.electrical)
        preview = self.preview()
        self.assertTrue(preview['can_apply'])
        self.assertEqual(preview['changed_count'], 0)
        self.assertEqual(self.apply(preview).get_json()['changed_count'], 0)
        self.assertEqual(self.tasks()[tid]['version'], 1)

    def test_unclassified_and_multiple_specialties_do_not_block(self):
        unassigned = self.task()
        multiple = self.task(self.member('机械', '视觉'))
        other = self.task(self.member('硬件'))
        preview = self.preview(base_date='2026-10-20')
        self.assertTrue(preview['can_apply'])
        self.assertEqual(set(preview['task_ids']), {unassigned, multiple, other})
        self.assertEqual(self.apply(preview).status_code, 200)
        self.assertIsNone(self.tasks()[multiple]['schedule_stage'])

    def test_fixed_and_in_progress_are_protected_but_explicitly_adjustable(self):
        fixed = self.task(self.mechanical, schedule_locked=True)
        active = self.task(self.electrical, status='进行中')
        ordinary = self.task(self.vision, status='阻塞', blocker_reason='等待联调设备', blocker_next_action='借用设备后继续联调')
        completed = self.task(status='已完成')
        cancelled = self.task(status='已取消')
        preview = self.preview(base_date='2026-10-20')
        self.assertEqual(preview['task_ids'], [ordinary])
        self.assertEqual(self.rows(preview)[fixed]['state'], 'protected')
        self.assertFalse(self.rows(preview)[completed]['eligible'])
        self.assertFalse(self.rows(preview)[cancelled]['eligible'])
        preview = self.preview(base_date='2026-10-20', task_ids=[fixed, active])
        self.assertEqual(self.apply(preview).status_code, 200)
        result = self.tasks()
        self.assertTrue(result[fixed]['schedule_locked'])
        self.assertEqual(result[active]['status'], '进行中')
        self.assertEqual(result[fixed]['start_date'], '2026-10-20')
        self.assertEqual(result[ordinary]['start_date'], '2026-10-14')

    def test_empty_selection_and_unselected_predecessor_remain_unchanged(self):
        predecessor = self.task(self.mechanical)
        successor = self.task(self.electrical, '2026-10-15', '2026-10-17', parent_id=predecessor)
        self.assertFalse(self.preview(task_ids=[])['can_apply'])
        preview = self.preview(base_date='2026-10-20', task_ids=[successor])
        self.assertEqual(self.rows(preview)[successor]['after']['start_date'], '2026-10-20')
        self.assertEqual(self.apply(preview).status_code, 200)
        self.assertEqual(self.tasks()[predecessor]['start_date'], '2026-10-14')

    def test_missing_or_invalid_interval_requires_duration_and_retains_saved_date_bounds(self):
        missing = self.task(self.mechanical, '', '2026-10-22')
        invalid = self.task(self.electrical)
        self.sql('UPDATE tasks SET start_date=?,due_date=? WHERE id=?', ('2026-10-25', '2026-10-21', invalid))
        preview = self.preview()
        self.assertFalse(preview['can_apply'])
        self.assertEqual(self.rows(preview)[missing]['state'], 'needs_duration')
        preview = self.preview(durations={str(missing): 3, str(invalid): 2})
        self.assertTrue(preview['can_apply'], preview)
        rows = self.rows(preview)
        self.assertTrue(rows[missing]['needs_duration'], 'keep the editable duration input after preview')
        self.assertEqual(rows[missing]['after'], {'start_date': '2026-10-20', 'due_date': '2026-10-22'})
        self.assertEqual(rows[invalid]['after'], {'start_date': '2026-10-25', 'due_date': '2026-10-26'})

    def test_existing_duration_cannot_be_changed_and_stale_drafts_are_normalized(self):
        good = self.task()
        completed = self.task(status='已完成')
        preview = self.preview(task_ids=[good, completed, 999999], durations={str(good): 999, str(completed): 5, '999999': 3})
        self.assertEqual(preview['task_ids'], [good])
        self.assertEqual(preview['durations'], {})
        self.assertEqual(self.rows(preview)[good]['duration_days'], 9)
        self.assertTrue(preview['warnings'])

    def test_completed_predecessor_uses_actual_completion_then_plan_fallback(self):
        completed = self.task(self.mechanical, '2026-10-14', '2026-10-30', status='已完成')
        successor = self.task(self.electrical, '2026-10-15', '2026-10-17', parent_id=completed)
        self.sql("UPDATE tasks SET start_date='',due_date='' WHERE id=?", (successor,))
        self.sql('UPDATE tasks SET completed_at=? WHERE id=?', ('2026-10-20 12:00:00', completed))
        preview = self.preview(durations={str(successor): 3})
        self.assertEqual(self.rows(preview)[successor]['after']['start_date'], '2026-10-18')
        for actual in (None, '2026-10-20 nonsense'):
            self.sql('UPDATE tasks SET completed_at=? WHERE id=?', (actual, completed))
            preview = self.preview(durations={str(successor): 3})
            self.assertEqual(self.rows(preview)[successor]['after']['start_date'], '2026-10-28')
            self.assertTrue(any('实际完成日期' in warning['message'] for warning in preview['warnings']))

    def test_external_predecessor_readonly_and_snapshot_covers_dates_and_edges(self):
        second = self.admin.post('/api/projects', json={'name': '外部车组' + self.marker}).get_json()['id']
        external = self.task(self.mechanical, end='2026-10-25', project_id=second)
        child = self.task(self.electrical, '2026-10-15', '2026-10-17', parent_id=external)
        self.sql("UPDATE tasks SET start_date='',due_date='' WHERE id=?", (child,))
        preview = self.preview(durations={str(child): 3})
        row = self.rows(preview)[child]
        self.assertEqual(row['after']['start_date'], '2026-10-23')
        self.assertTrue(row['predecessors'][0]['external'])
        self.sql('UPDATE tasks SET due_date=? WHERE id=?', ('2026-10-26', external))
        self.assertEqual(self.apply(preview).status_code, 409)
        preview = self.preview(durations={str(child): 3})
        ancestor = self.task(self.vision, end='2026-10-14', project_id=second)
        self.sql('INSERT INTO task_dependencies(task_id,predecessor_id) VALUES(?,?)', (external, ancestor))
        self.assertEqual(self.apply(preview).status_code, 409)
        before = self.tasks()[external]
        preview = self.preview(durations={str(child): 3})
        self.assertEqual(self.apply(preview).status_code, 200)
        self.assertEqual(self.tasks()[external], before)

    def test_local_shift_warns_for_intermediate_external_reservations(self):
        first = self.task(self.mechanical)
        second = self.admin.post('/api/projects', json={'name': '外部中间车组' + self.marker}).get_json()['id']
        external = self.task(self.electrical, '2026-10-20', '2026-10-25', project_id=second, parent_id=first)
        last = self.task(self.vision, '2026-10-23', '2026-10-25', parent_id=external)
        preview = self.preview(base_date='2026-11-01')
        self.assertTrue(preview['can_apply'], preview)
        self.assertEqual(self.rows(preview)[last]['after']['start_date'], '2026-11-01')
        self.assertIsNotNone(self.rows(preview)[last]['predecessors'][0]['boundary_date'])
        self.assertTrue(any(w.get('task_id') == external and w.get('kind') == 'dependency_early_reservation'
                            for w in preview['warnings']))
        self.assertEqual(self.tasks()[first]['start_date'], '2026-10-14')

    def test_cycles_invalid_cancelled_and_missing_date_predecessors_block(self):
        predecessor = self.task(self.mechanical)
        child = self.task(self.electrical, parent_id=predecessor)
        self.sql('INSERT INTO task_dependencies(task_id,predecessor_id) VALUES(?,?)', (predecessor, child))
        preview = self.preview()
        self.assertFalse(preview['can_apply'])
        self.assertEqual({error['task_id'] for error in preview['errors']}, {predecessor, child})
        self.sql('DELETE FROM task_dependencies WHERE task_id=?', (predecessor,))
        self.sql("UPDATE tasks SET status='已取消' WHERE id=?", (predecessor,))
        self.assertFalse(self.preview()['can_apply'])
        self.sql("UPDATE tasks SET status='未开始',deleted_at='2026-10-15' WHERE id=?", (predecessor,))
        self.assertFalse(self.preview()['can_apply'])
        self.sql("UPDATE tasks SET deleted_at=NULL,start_date='',due_date='' WHERE id=?", (predecessor,))
        self.assertFalse(self.preview(task_ids=[child])['can_apply'])

    def test_protected_early_reservation_is_warning_not_blocker(self):
        predecessor = self.task(self.mechanical, '2026-10-20', '2026-10-25')
        fixed = self.task(self.electrical, '2026-10-14', '2026-10-16', parent_id=predecessor, schedule_locked=True)
        preview = self.preview()
        self.assertTrue(preview['can_apply'], preview)
        self.assertEqual(self.rows(preview)[fixed]['state'], 'protected')
        self.assertEqual(self.rows(preview)[fixed]['after']['start_date'], '2026-10-14')
        self.assertTrue(any(w.get('kind') == 'dependency_early_reservation' and w['task_id'] == fixed
                            for w in preview['warnings']))
        self.assertTrue(self.preview(task_ids=[predecessor, fixed])['can_apply'])

    def test_owner_overlap_warns_without_automatic_resource_scheduling(self):
        first = self.task(self.mechanical)
        second = self.task(self.mechanical)
        preview = self.preview()
        self.assertTrue(preview['can_apply'])
        warning = next(item for item in preview['warnings'] if '日期重叠' in item['message'])
        self.assertEqual(set(warning['related_task_ids']), {first, second})
        self.assertEqual(preview['changed_count'], 0)

    def test_date_overflow_blocks_entire_apply(self):
        predecessor = self.task(self.mechanical, '9999-12-31', '9999-12-31')
        child = self.task(self.electrical, '2026-10-14', '2026-10-15', parent_id=predecessor)
        self.sql("UPDATE tasks SET start_date='',due_date='' WHERE id=?", (child,))
        preview = self.preview(durations={str(child): 5})
        self.assertFalse(preview['can_apply'])
        self.assertEqual(self.rows(preview)[child]['state'], 'conflict')
        self.assertEqual(self.tasks()[child]['version'], 1)

    def test_task_edits_creation_deletion_and_fixed_flag_invalidate_snapshot(self):
        first = self.task(self.mechanical)
        preview = self.preview()
        self.assertEqual(self.admin.post(f'/api/task/{first}', json={'title': '更名', 'version': 1}).status_code, 200)
        self.assertEqual(self.apply(preview).status_code, 409)
        preview = self.preview()
        extra = self.task(self.vision)
        self.assertEqual(self.apply(preview).status_code, 409)
        preview = self.preview()
        self.admin.post(f'/api/task/{extra}/delete', json={'version': 1})
        self.assertEqual(self.apply(preview).status_code, 409)
        preview = self.preview()
        self.sql('UPDATE tasks SET schedule_locked=1 WHERE id=?', (first,))
        self.assertEqual(self.apply(preview).status_code, 409)

    def test_permissions_csrf_old_algorithm_and_signed_preview_validation(self):
        self.task(self.mechanical)
        preview = self.preview()
        member = app_module.app.test_client()
        with app_module.app.app_context():
            username = get_db().execute('SELECT username FROM members WHERE id=?', (self.vision,)).fetchone()[0]
        member.post('/login', json={'username': username, 'password': 'schedule-member-password'})
        for action in ('preview', 'apply'):
            endpoint = f'/api/projects/{self.project}/schedule/{action}'
            self.assertEqual(member.post(endpoint, json={}).status_code, 403)
            self.assertEqual(app_module.app.test_client().post(endpoint, json={}).status_code, 401)
        endpoint = f'/api/projects/{self.project}/schedule/apply'
        self.assertEqual(self.admin.post(endpoint, json={'snapshot': preview['snapshot']}, headers={'X-CSRF-Token': 'invalid'}).status_code, 403)
        self.assertEqual(self.admin.post(endpoint, json={'snapshot': preview['snapshot'] + '!'}).status_code, 400)
        old_token = URLSafeSerializer(app_module.app.secret_key, salt='vehicle-task-schedule-v1').dumps({'project_id': self.project})
        self.assertEqual(self.admin.post(endpoint, json={'snapshot': old_token}).status_code, 400)
        self.assertEqual(self.admin.post(f'/api/projects/{self.project}/schedule/preview', json={}).status_code, 400)
        other = self.admin.post('/api/projects', json={'name': 'token车组' + self.marker}).get_json()['id']
        self.assertEqual(self.admin.post(f'/api/projects/{other}/schedule/apply', json={'snapshot': preview['snapshot']}).status_code, 400)

    def test_invalid_parameters_and_archived_project(self):
        tid = self.task()
        endpoint = f'/api/projects/{self.project}/schedule/preview'
        for parameters in ({'algorithm_version': 1}, {'algorithm_version': True}, {'base_date': '20261020'},
                           {'task_ids': None}, {'task_ids': [True]}, {'durations': []},
                           {'durations': {str(tid): 0}}, {'durations': {str(tid): True}}):
            response = self.admin.post(endpoint, json={'algorithm_version': 2, **parameters})
            self.assertEqual(response.status_code, 400, parameters)
        self.assertEqual(self.admin.post('/api/projects/999999/schedule/preview', json={'algorithm_version': 2}).status_code, 404)
        archived = self.admin.post('/api/projects', json={'name': '已归档排期' + self.marker}).get_json()['id']
        self.admin.post(f'/api/projects/{archived}/delete', json={'confirm_name': '已归档排期' + self.marker})
        self.assertEqual(self.admin.post(f'/api/projects/{archived}/schedule/preview', json={'algorithm_version': 2}).status_code, 404)

    def test_batch_failure_rolls_back_dates_versions_revisions_and_logs(self):
        first = self.task(self.electrical, '2026-10-15', '2026-10-17')
        second = self.task(self.vision, '2026-10-15', '2026-10-18')
        preview = self.preview(base_date='2026-10-20')
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
        for tid in (first, second):
            self.assertEqual(self.tasks()[tid], before[tid])


if __name__ == '__main__':
    unittest.main()
