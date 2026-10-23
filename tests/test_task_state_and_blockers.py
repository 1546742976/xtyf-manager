"""Task transitions and blocker lifecycle through the real isolated Flask API."""
import io
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from PIL import Image
from test_tasks import app_module
from common import get_db
from modules.task_migrations import migrate


class TaskStateAndBlockerTests(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})

    def create(self, **fields):
        response = self.admin.post('/api/task', json={'title': uuid4().hex, **fields})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['id']

    def task(self, tid):
        return next(t for t in self.admin.get('/api/data').get_json()['tasks'] if t['id'] == tid)

    def update(self, tid, **fields):
        return self.admin.post(f'/api/task/{tid}', json={'version': self.task(tid)['version'], **fields})

    def progress(self, tid, percent, **fields):
        return self.admin.post(f'/api/task/{tid}/progress', data={
            'version': str(self.task(tid)['version']), 'progress': str(percent), **fields})

    def block(self, tid, **fields):
        return self.admin.post(f'/api/task/{tid}/blocker', json={
            'version': self.task(tid)['version'], 'blocker_action': 'open',
            'blocker_reason': '等待驱动板', 'blocker_next_action': '负责人联系加工方', **fields})

    def events(self, tid):
        return self.admin.get(f'/api/task/{tid}/blocker').get_json()['blocker_events']

    def member(self):
        name = 'state_' + uuid4().hex[:12]
        mid = self.admin.post('/api/member', json={'name': name, 'username': name, 'password': 'state-password'}).get_json()['id']
        client = app_module.app.test_client()
        client.post('/login', json={'username': name, 'password': 'state-password'})
        return mid, client

    def test_creation_and_first_real_progress(self):
        finished = self.create(status='已完成')
        self.assertEqual(self.task(finished)['progress'], 100)
        self.assertIsNotNone(self.task(finished)['completed_at'])
        tid = self.create()
        self.assertEqual(self.progress(tid, 0, note='准备材料').status_code, 200)
        self.assertEqual(self.task(tid)['status'], '未开始')
        response = self.progress(tid, 1)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['status'], '进行中')
        self.assertEqual(self.task(tid)['status'], '进行中')

    def test_completion_progress_and_explicit_reopen_are_atomic(self):
        tid = self.create()
        with patch('modules.task_progress.completion_stamp', return_value='2026-10-18 00:01:00'):
            completed = self.progress(tid, 100)
        self.assertEqual(completed.status_code, 200, completed.get_json())
        current = self.task(tid)
        self.assertEqual((current['status'], current['progress'], current['completed_at']), ('已完成', 100, '2026-10-18 00:01:00'))
        for body in ({'status': '进行中'}, {'status': '进行中', 'progress': 100}, {'status': '已完成', 'progress': 80}):
            self.assertEqual(self.update(tid, **body).status_code, 400)
            self.assertEqual(self.task(tid)['version'], current['version'])
        reopened = self.progress(tid, 73)
        self.assertEqual(reopened.status_code, 200, reopened.get_json())
        self.assertEqual((self.task(tid)['status'], self.task(tid)['progress'], self.task(tid)['completed_at']), ('进行中', 73, None))
        self.assertEqual(self.update(tid, status='已完成').status_code, 200)
        self.assertEqual(self.task(tid)['progress'], 100)
        self.assertEqual(self.update(tid, status='进行中', progress=30).status_code, 200)

    def test_cancelled_task_requires_explicit_reopen(self):
        tid = self.create(status='已取消')
        for percent in (0, 40, 100):
            self.assertEqual(self.progress(tid, percent, note='不能自动恢复').status_code, 400)
        self.assertEqual(self.update(tid, status='进行中').status_code, 400)
        self.assertEqual(self.update(tid, status='进行中', progress=25).status_code, 200)
        self.assertEqual(self.task(tid)['progress'], 25)

    def test_transition_requires_version_and_stale_progress_does_not_write(self):
        tid = self.create()
        self.assertEqual(self.admin.post(f'/api/task/{tid}', json={'status': '已完成'}).status_code, 400)
        old = self.task(tid)['version']
        self.assertEqual(self.update(tid, title='并发改名').status_code, 200)
        response = self.progress(tid, 100, version=old)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.task(tid)['progress'], 0)
        self.assertEqual(self.admin.get(f'/api/task/{tid}/progress').get_json()['updates'], [])

    def test_blocker_required_fields_and_optional_values(self):
        for missing in ('blocker_reason', 'blocker_next_action'):
            response = self.admin.post('/api/task', json={'title': uuid4().hex, 'status': '阻塞',
                'blocker_reason': 'r', 'blocker_next_action': 'n', missing: ''})
            self.assertEqual(response.status_code, 400)
        tid = self.create()
        for invalid in ({'blocker_follow_up': '2026-02-30'}, {'blocker_helper_id': True}, {'blocker_helper_id': 999999999}):
            self.assertEqual(self.block(tid, **invalid).status_code, 400)
        self.assertEqual(self.events(tid), [])
        mid, _ = self.member()
        self.assertEqual(self.block(tid, blocker_helper_id=mid, blocker_follow_up='2026-10-19').status_code, 200)
        task = self.task(tid)
        self.assertEqual((task['status'], task['blocker_helper_id'], task['blocker_follow_up']), ('阻塞', mid, '2026-10-19'))
        self.assertEqual(self.events(tid)[0]['action'], 'open')

    def test_blocker_manual_resolution_preserves_progress(self):
        tid = self.create()
        self.progress(tid, 43)
        self.block(tid)
        self.assertEqual(self.progress(tid, 44).status_code, 200)
        self.assertEqual(self.task(tid)['status'], '阻塞')
        self.assertEqual(self.block(tid, blocker_action='resolve', blocker_result='').status_code, 400)
        result = self.block(tid, blocker_action='resolve', blocker_result='驱动板到货')
        self.assertEqual(result.status_code, 200, result.get_json())
        task = self.task(tid)
        self.assertEqual((task['status'], task['progress'], task['blocker_reason']), ('进行中', 44, ''))
        self.assertEqual(self.events(tid)[0]['resolution'], '驱动板到货')

    def test_blocker_edit_stale_version_and_legacy_empty_fields(self):
        tid = self.create()
        with app_module.app.app_context():
            db = get_db(); db.execute("UPDATE tasks SET status='阻塞' WHERE id=?", (tid,)); db.commit()
        self.assertEqual(self.update(tid, title='历史任务更名', status='阻塞', blocker_reason='',
            blocker_next_action='', blocker_helper_id=None, blocker_follow_up='').status_code, 200)
        self.assertEqual(self.events(tid), [])
        self.assertEqual(self.block(tid, blocker_action='update').status_code, 200)
        old = self.task(tid)['version']
        self.assertEqual(self.block(tid, blocker_action='update', blocker_next_action='已约定交付日期').status_code, 200)
        latest = self.task(tid)
        self.assertEqual(self.block(tid, version=old, blocker_action='resolve', blocker_result='过期页面').status_code, 409)
        self.assertEqual(self.task(tid)['version'], latest['version'])
        self.assertEqual([event['action'] for event in self.events(tid)], ['update', 'update'])

    def test_blocker_completion_and_cancel_record_distinct_system_results(self):
        for status, action in (('已完成', 'resolve'), ('已取消', 'cancel')):
            with self.subTest(status=status):
                tid = self.create(status='阻塞', blocker_reason='缺料', blocker_next_action='采购')
                result = self.progress(tid, 100) if status == '已完成' else self.update(tid, status=status)
                self.assertEqual(result.status_code, 200, result.get_json())
                self.assertEqual(self.task(tid)['status'], status)
                self.assertEqual(self.events(tid)[0]['action'], action)
                self.assertTrue(self.events(tid)[0]['resolution'])

    def test_helper_has_no_new_write_permission_and_participant_keeps_permission(self):
        helper, client = self.member()
        tid = self.create()
        self.block(tid, blocker_helper_id=helper)
        self.assertEqual(client.post(f'/api/task/{tid}/blocker', json={
            'version': self.task(tid)['version'], 'blocker_action': 'resolve', 'blocker_result': '越权'}).status_code, 403)
        self.assertEqual(client.post(f'/api/task/{tid}/progress', data={
            'version': self.task(tid)['version'], 'progress': '100'}).status_code, 403)
        self.assertEqual(self.update(tid, owner_id=helper, participant_ids=[helper]).status_code, 200)
        self.assertEqual(client.post(f'/api/task/{tid}/progress', data={
            'version': self.task(tid)['version'], 'progress': '100'}).status_code, 200)

    def test_inactive_existing_helper_is_preserved_without_allowing_new_assignments(self):
        helper, _ = self.member()
        tid = self.create()
        self.assertEqual(self.block(tid, blocker_helper_id=helper).status_code, 200)
        with app_module.app.app_context():
            db = get_db(); db.execute('UPDATE members SET is_active=0 WHERE id=?', (helper,)); db.commit()
        current = self.task(tid)
        response = self.update(tid, title='保留已停用协助人', status='阻塞',
            **{key: current[key] for key in ('blocker_reason', 'blocker_next_action', 'blocker_helper_id', 'blocker_follow_up')})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.task(tid)['blocker_helper_id'], helper)
        response = self.block(tid, blocker_action='update', blocker_helper_id=helper,
            blocker_next_action='协助人停用后由原负责人继续跟进')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.task(tid)['blocker_helper_id'], helper)
        other = self.create()
        self.assertEqual(self.block(other, blocker_helper_id=helper).status_code, 400)
        self.assertEqual(self.task(other)['status'], '未开始')

    def test_revision_revert_restores_complete_group_and_blocks_later_progress(self):
        tid = self.create(status='阻塞', blocker_reason='缺料', blocker_next_action='采购')
        self.progress(tid, 30)
        self.progress(tid, 100)
        response = self.admin.get(f'/api/task/{tid}/revisions')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertIn('revisions', response.get_json(), response.get_json())
        revisions = response.get_json()['revisions']
        completion = revisions[0]
        result = self.admin.post(f'/api/task/{tid}/revisions/{completion["id"]}/revert', json={'version': self.task(tid)['version']})
        self.assertEqual(result.status_code, 200, result.get_json())
        task = self.task(tid)
        self.assertEqual((task['status'], task['progress'], task['completed_at'], task['blocker_reason']), ('阻塞', 30, None, '缺料'))
        self.progress(tid, 40)
        old_progress = revisions[1]
        denied = self.admin.post(f'/api/task/{tid}/revisions/{old_progress["id"]}/revert', json={'version': self.task(tid)['version']})
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(self.task(tid)['progress'], 40)

    def test_legacy_conflicts_are_not_automatically_repaired(self):
        tid = self.create()
        with app_module.app.app_context():
            db = get_db()
            db.execute("UPDATE tasks SET status='已完成',progress=12,completed_at=NULL WHERE id=?", (tid,))
            revision = db.execute('INSERT INTO task_revisions(task_id,actor_id,actor_name,created_at,before_json,after_json,version,kind) VALUES(?,?,?,?,?,?,?,?)',
                (tid, 1, '历史', 'old', json.dumps({'status': '进行中'}), json.dumps({'status': '已完成'}), 1, 'update')).lastrowid
            db.commit()
            migrate(db)
        self.assertEqual(self.update(tid, title='只改标题', status='已完成').status_code, 200)
        task = self.task(tid)
        self.assertEqual((task['status'], task['progress'], task['completed_at']), ('已完成', 12, None))
        self.assertEqual(self.admin.post(f'/api/task/{tid}/revisions/{revision}/revert', json={'version': task['version']}).status_code, 409)

    def test_legacy_not_started_form_resave_does_not_start_and_resolve_always_starts(self):
        tid = self.create()
        with app_module.app.app_context():
            db = get_db(); db.execute('UPDATE tasks SET progress=30 WHERE id=?', (tid,)); db.commit()
        self.assertEqual(self.update(tid, title='保留历史', status='未开始').status_code, 200)
        self.assertEqual((self.task(tid)['status'], self.task(tid)['progress']), ('未开始', 30))
        self.assertEqual(self.block(tid).status_code, 200)
        self.assertEqual(self.update(tid, status='未开始', blocker_result='问题已解决').status_code, 200)
        self.assertEqual(self.task(tid)['status'], '进行中')

    def test_image_failure_rolls_back_completion_and_audit(self):
        tid = self.create(status='阻塞', blocker_reason='缺料', blocker_next_action='采购')
        before = self.task(tid)
        image = io.BytesIO(); Image.new('RGB', (10, 10), 'red').save(image, format='PNG'); image.seek(0)
        with patch('modules.task_progress.finalize_staged', side_effect=OSError('disk full')):
            response = self.progress(tid, 100, files=(image, 'result.png', 'image/png'))
        self.assertEqual(response.status_code, 500)
        after = self.task(tid)
        self.assertEqual((after['status'], after['progress'], after['version']), (before['status'], before['progress'], before['version']))
        self.assertEqual([event['action'] for event in self.events(tid)], ['open'])
        self.assertEqual(self.admin.get(f'/api/task/{tid}/progress').get_json()['updates'], [])


if __name__ == '__main__':
    unittest.main()
