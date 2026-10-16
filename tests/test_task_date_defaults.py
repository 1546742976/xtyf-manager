"""One-time task date defaults through real APIs, revisions and permissions."""
import unittest
from uuid import uuid4

from test_tasks import app_module
from modules.task_dates import dependency_start_date


class TaskDateDefaults(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})

    def create(self, **data):
        response = self.admin.post('/api/task', json={'title': '日期测试' + uuid4().hex[:8], **data})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def task(self, task_id):
        return next(task for task in self.admin.get('/api/data').get_json()['tasks'] if task['id'] == task_id)

    def member(self):
        username = 'dates_' + uuid4().hex[:12]
        result = self.admin.post('/api/member', json={'name': username, 'username': username, 'password': 'date-test-password'})
        client = app_module.app.test_client()
        client.post('/login', json={'username': username, 'password': 'date-test-password'})
        return result.get_json()['id'], client

    def test_calendar_arithmetic_month_year_and_leap_boundaries(self):
        cases = {'2024-03-01': '2024-02-28', '2024-03-02': '2024-02-29',
                 '2026-03-01': '2026-02-27', '2026-01-01': '2025-12-30',
                 '2026-03-09': '2026-03-07'}
        for source, expected in cases.items():
            self.assertEqual(dependency_start_date(source), expected)
        for bad in ('', None, '2026-02-30', '2026-3-01', '0001-01-01'):
            self.assertIsNone(dependency_start_date(bad))

    def test_overlapping_predecessor_dates_are_valid_and_real_warnings_remain(self):
        parent = self.create(due_date='2026-11-03')
        # Both an explicit early date and the suggested two-day overlap are valid.
        early = self.create(parent_id=parent['id'], start_date='2026-10-01', due_date='2026-11-08')
        suggested = self.create(parent_id=parent['id'], due_date='2026-11-08')
        self.assertEqual(self.task(suggested['id'])['start_date'], '2026-11-01')
        for task_id in (early['id'], suggested['id']):
            self.assertEqual(self.task(task_id)['dependency_warnings'], [])

        # An actually blocked or cancelled predecessor still needs attention.
        endpoint = f"/api/task/{parent['id']}"
        self.assertEqual(self.admin.post(endpoint, json={'version': 1, 'status': '阻塞'}).status_code, 200)
        self.assertTrue(any('已阻塞' in warning for warning in self.task(early['id'])['dependency_warnings']))
        self.assertEqual(self.admin.post(endpoint, json={
            'version': 2, 'status': '进行中', 'due_date': ''
        }).status_code, 200)
        self.assertTrue(any('未设置截止日期' in warning
                            for warning in self.task(early['id'])['dependency_warnings']))
        self.assertEqual(self.admin.post(endpoint, json={'version': 3, 'status': '已取消'}).status_code, 200)
        self.assertTrue(any('已取消' in warning for warning in self.task(early['id'])['dependency_warnings']))

        # Completing a successor while its predecessor is unfinished is still unusual.
        pending = self.create(due_date='2026-12-10')
        completed = self.create(parent_id=pending['id'], due_date='2026-12-20', status='已完成')
        self.assertTrue(any('尚未完成' in warning
                            for warning in self.task(completed['id'])['dependency_warnings']))

    def test_create_uses_only_main_parent_and_fixes_first_saved_date(self):
        parent = self.create(due_date='2026-11-03')
        other = self.create(due_date='2026-12-10')
        child = self.create(parent_id=parent['id'], predecessor_ids=[other['id']])
        before = self.task(child['id'])
        self.assertEqual((before['start_date'], before['start_date_origin']), ('2026-11-01', 'dependency'))
        self.assertEqual(before['suggested_start_date'], '2026-11-01')
        self.admin.post(f"/api/task/{parent['id']}", json={'version': 1, 'due_date': '2026-11-20'})
        after = self.task(child['id'])
        self.assertEqual((after['start_date'], after['version']), ('2026-11-01', 1))
        self.assertEqual(after['suggested_start_date'], '2026-11-18')
        other_only = self.create(predecessor_ids=[other['id']])
        self.assertEqual(self.task(other_only['id'])['start_date'], '')
        no_date_parent = self.create()
        self.assertEqual(self.task(self.create(parent_id=no_date_parent['id'])['id'])['start_date'], '')

    def test_custom_empty_and_equal_dates_are_explicit_and_reset_is_revisioned(self):
        parent = self.create(due_date='2026-11-03')
        child = self.create(parent_id=parent['id'], start_date='', start_date_origin='manual')
        self.assertEqual(self.task(child['id'])['start_date'], '')
        endpoint = f"/api/task/{child['id']}"
        reset = self.admin.post(endpoint, json={'version': 1, 'reset_start_date': True})
        self.assertEqual(reset.status_code, 200, reset.get_json())
        current = self.task(child['id'])
        self.assertEqual((current['start_date'], current['start_date_origin']), ('2026-11-01', 'dependency'))
        revision = self.admin.get(endpoint + '/revisions').get_json()['revisions'][0]
        self.assertEqual(revision['before']['start_date_origin'], 'manual')
        self.assertEqual(self.admin.post(endpoint + f"/revisions/{revision['id']}/revert", json={'version': 2}).status_code, 200)
        reverted = self.task(child['id'])
        self.assertEqual((reverted['start_date'], reverted['start_date_origin']), ('', 'manual'))
        # Equality with a suggested date does not infer the source.
        explicit = self.create(parent_id=parent['id'], start_date='2026-11-01')
        self.assertEqual(self.task(explicit['id'])['start_date_origin'], 'manual')

    def test_existing_parent_change_does_not_fill_or_replace_date(self):
        parent = self.create(due_date='2026-11-03')
        for initial in ('', '2026-10-10'):
            child = self.create(start_date=initial)
            self.assertEqual(self.admin.post(f"/api/task/{child['id']}", json={'version': 1, 'parent_id': parent['id']}).status_code, 200)
            self.assertEqual(self.task(child['id'])['start_date'], initial)

    def test_editors_may_reset_and_edit_dependencies_unrelated_members_cannot(self):
        owner_id, owner = self.member()
        participant_id, participant = self.member()
        _, outsider = self.member()
        parent = self.create(due_date='2026-11-03')
        additional = self.create()
        child = self.create(owner_id=owner_id, participant_ids=[participant_id])
        endpoint = f"/api/task/{child['id']}"
        data = {'version': 1, 'parent_id': parent['id'], 'predecessor_ids': [additional['id']], 'reset_start_date': True}
        self.assertEqual(outsider.post(endpoint, json=data).status_code, 403)
        self.assertEqual(participant.post(endpoint, json=data).status_code, 200)
        self.assertEqual(owner.post(endpoint, json=data).status_code, 409)
        self.assertEqual(owner.post(endpoint, json={'version': 2, 'start_date': '2026-10-20'}).status_code, 200)
        self.assertEqual(self.task(child['id'])['start_date_origin'], 'manual')
        cyclic = self.admin.post(f"/api/task/{parent['id']}", json={'version': 1, 'parent_id': child['id']})
        self.assertEqual(cyclic.status_code, 400)

    def test_invalid_reset_or_result_rolls_back_all_other_fields(self):
        parent = self.create(due_date='2026-11-03')
        child = self.create(parent_id=parent['id'], start_date='2026-10-01', due_date='2026-10-10')
        original = self.task(child['id'])
        endpoint = f"/api/task/{child['id']}"
        for fields in ({'reset_start_date': True}, {'reset_start_date': 'true'},
                       {'start_date_origin': 'dependency'}, {'parent_id': None, 'reset_start_date': True}):
            response = self.admin.post(endpoint, json={'version': 1, 'title': '不得保存', **fields})
            self.assertEqual(response.status_code, 400, response.get_json())
            self.assertEqual(self.task(child['id']), original)


if __name__ == '__main__':
    unittest.main()
