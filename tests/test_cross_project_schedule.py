"""Owner conflicts across vehicle projects use proposed dates and signed snapshots."""
import unittest
from uuid import uuid4

from test_tasks import app_module
from common import get_db


class TestCrossProjectSchedule(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.assertEqual(self.admin.post('/login', json={'username': 'admin', 'password': '123456'}).status_code, 200)
        marker = uuid4().hex[:12]
        self.project = self.admin.post('/api/projects', json={'name': '排期本组' + marker}).get_json()['id']
        self.external_project = self.admin.post('/api/projects', json={'name': '排期外组' + marker}).get_json()['id']
        member = self.admin.post('/api/member', json={'name': '负责人' + marker, 'username': 'conflict_' + marker,
            'password': 'schedule-conflict-password', 'role': 'member'})
        self.assertEqual(member.status_code, 200, member.get_json())
        self.owner = member.get_json()['id']

    def task(self, *, external=False, **extra):
        response = self.admin.post('/api/task', json={'title': '冲突核对' + uuid4().hex[:6],
            'owner_id': self.owner, 'project_id': self.external_project if external else self.project,
            'start_date': '2026-10-14', 'due_date': '2026-10-16', **extra})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['id']

    def preview(self, **extra):
        response = self.admin.post(f'/api/projects/{self.project}/schedule/preview', json={
            'algorithm_version': 2, 'base_date': '2026-10-14', **extra})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def apply(self, preview):
        return self.admin.post(f'/api/projects/{self.project}/schedule/apply', json={'snapshot': preview['snapshot']})

    def sql(self, statement, parameters=()):
        with app_module.app.app_context():
            db = get_db()
            db.execute(statement, parameters)
            db.commit()

    def saved(self, tid):
        with app_module.app.app_context():
            return dict(get_db().execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone())

    def overlaps(self, preview):
        return [item for item in preview['warnings'] if item.get('kind') == 'owner_overlap']

    def test_proposed_dates_inclusive_endpoint_and_external_task_stays_readonly(self):
        local = self.task()
        external = self.task(external=True, start_date='2026-10-22', due_date='2026-10-22')
        before = self.saved(external)
        preview = self.preview(base_date='2026-10-20')
        self.assertTrue(preview['can_apply'], preview)
        self.assertEqual({row['id'] for row in preview['rows']}, {local})
        warning, = self.overlaps(preview)
        self.assertEqual(warning['related_task_ids'], [local, external])
        self.assertEqual((warning['overlap_start'], warning['overlap_end']), ('2026-10-22', '2026-10-22'))
        self.assertEqual(warning['related_tasks'][1]['project_id'], self.external_project)
        self.assertTrue(warning['related_tasks'][1]['external'])
        self.assertEqual(self.apply(preview).status_code, 200)
        self.assertEqual(self.saved(external), before)
        self.assertEqual(self.saved(local)['start_date'], '2026-10-20')

    def test_ungrouped_tasks_included_but_nonoverlap_and_unassigned_tasks_excluded(self):
        local = self.task()
        ungrouped = self.task(project_id=None, start_date='2026-10-16', due_date='2026-10-17')
        self.task(external=True, start_date='2026-10-17', due_date='2026-10-18')
        self.task(external=True, owner_id=None)
        warning, = self.overlaps(self.preview())
        self.assertEqual(warning['related_task_ids'], [local, ungrouped])
        self.assertEqual(warning['related_tasks'][1]['project_name'], '未归属造车组别')

    def test_terminal_deleted_invalid_dates_and_different_owners_do_not_warn(self):
        local = self.task()
        peer = self.task()
        self.task(external=True, status='已完成')
        self.task(external=True, status='已取消')
        deleted = self.task(external=True)
        self.sql('UPDATE tasks SET deleted_at=? WHERE id=?', ('2026-10-14 00:00:00', deleted))
        invalid = self.task(external=True)
        self.sql('UPDATE tasks SET start_date=? WHERE id=?', ('2026-10-20', invalid))
        self.task(external=True, owner_id=1)
        warning, = self.overlaps(self.preview())
        self.assertEqual(warning['related_task_ids'], [local, peer])
        self.assertFalse(any(item['external'] for item in warning['related_tasks']))

    def test_external_changes_invalidate_snapshot_before_any_local_write(self):
        local = self.task()
        external = self.task(external=True, start_date='2026-12-01', due_date='2026-12-03')
        original = self.saved(external)
        mutations = {'due_date': '2026-12-04', 'owner_id': None, 'status': '已取消',
                     'deleted_at': '2026-10-14 00:00:00', 'project_id': None, 'title': '更名'}
        for field, value in mutations.items():
            with self.subTest(field=field):
                preview = self.preview(base_date='2026-10-20')
                before = self.saved(local)
                self.sql(f'UPDATE tasks SET {field}=? WHERE id=?', (value, external))
                self.assertEqual(self.apply(preview).status_code, 409)
                self.assertEqual(self.saved(local), before)
                self.sql(f'UPDATE tasks SET {field}=? WHERE id=?', (original[field], external))

    def test_new_or_reassigned_external_task_invalidates_snapshot(self):
        self.task()
        preview = self.preview(base_date='2026-10-20')
        self.task(external=True)
        self.assertEqual(self.apply(preview).status_code, 409)
        unrelated = self.task(external=True, owner_id=1)
        preview = self.preview(base_date='2026-10-20')
        self.sql('UPDATE tasks SET owner_id=? WHERE id=?', (self.owner, unrelated))
        self.assertEqual(self.apply(preview).status_code, 409)

    def test_unrelated_owner_changes_do_not_invalidate_and_external_pairs_are_not_reported(self):
        local = self.task()
        first = self.task(external=True)
        second = self.task(external=True)
        unrelated = self.task(external=True, owner_id=1)
        preview = self.preview(base_date='2026-10-15')
        self.assertEqual({tuple(item['related_task_ids']) for item in self.overlaps(preview)}, {(local, first), (local, second)})
        self.sql('UPDATE tasks SET due_date=? WHERE id=?', ('2026-12-30', unrelated))
        self.assertEqual(self.apply(preview).status_code, 200)

    def test_external_predecessor_is_not_duplicated_as_owner_conflict(self):
        external = self.task(external=True, start_date='2026-10-20', due_date='2026-10-22')
        local = self.task(parent_id=external)
        preview = self.preview()
        warning, = self.overlaps(preview)
        self.assertEqual(warning['related_task_ids'], [local, external])
        self.assertEqual({row['id'] for row in preview['rows']}, {local})


if __name__ == '__main__':
    unittest.main()
