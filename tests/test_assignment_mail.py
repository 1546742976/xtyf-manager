"""Assignment delivery through task APIs and the existing mail worker seam."""
import unittest
from datetime import datetime, timedelta
from uuid import uuid4

from test_tasks import app_module
from common import get_db
from modules.daily_projects import TZ
from modules.mail_service import process_job
from modules.mail_service import DeliveryError


class AssignmentMailTests(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app
        self.context = self.app.app_context(); self.context.push()
        self.addCleanup(self.context.pop)
        self.config = dict(self.app.config['MAIL_SETTINGS'])
        self.addCleanup(lambda: self.app.config.__setitem__('MAIL_SETTINGS', self.config))
        self.app.config['MAIL_SETTINGS'].update(mode='preview', base_url='https://team.example', sender='team@example.invalid')
        self.admin = self.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        self.db = get_db()
        name = 'assigned_' + uuid4().hex[:10]
        self.mid = self.admin.post('/api/member', json={'name': name, 'username': name, 'password': 'mail-password'}).get_json()['id']
        self.member = self.app.test_client()
        self.member.post('/login', json={'username': name, 'password': 'mail-password'})
        self.db.execute('INSERT INTO member_email(member_id,address,verified,generation,requested_at) VALUES(?,?,1,?,0)',
                        (self.mid, 'synthetic@example.invalid', uuid4().hex))
        self.db.commit()

    def jobs(self):
        return [j for j in self.admin.get('/api/admin/mail').get_json()['jobs'] if j['member_id'] == self.mid and j['kind'] == 'assignment']

    def deliver(self, job_id, at=None):
        job = dict(self.db.execute('SELECT * FROM mail_jobs WHERE id=?', (job_id,)).fetchone())
        job['attempts'] = 1
        messages = []
        result = process_job(self.db, job, at or datetime.now(TZ), lambda message: messages.append(message))
        return result, messages

    def test_new_executors_receive_one_mail_and_content_edits_do_not_requeue(self):
        created = self.admin.post('/api/task', json={'title': '分配电机调试', 'owner_id': self.mid, 'participant_ids': [self.mid]}).get_json()
        jobs = self.jobs()
        self.assertEqual(len(jobs), 1)
        state, messages = self.deliver(jobs[0]['id'])
        self.assertEqual(state, 'sent')
        self.assertEqual(len(messages), 1)
        self.assertIn('任务指派', str(messages[0]['Subject']))
        self.assertIn('分配电机调试', messages[0].get_body(preferencelist=('plain',)).get_content())
        edited = self.admin.post('/api/task/' + str(created['id']), json={'version': 1, 'title': '只修改说明'})
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(len(self.jobs()), 1)

    def test_preference_is_independent_and_legacy_save_preserves_it(self):
        pref = self.member.get('/api/account/email').get_json()['preferences']
        self.assertTrue(pref['assignment_enabled'])
        pref['assignment_enabled'] = False
        self.assertEqual(self.member.post('/api/account/email/preferences', json=pref).status_code, 200)
        del pref['assignment_enabled']
        pref['morning_enabled'] = False
        result = self.member.post('/api/account/email/preferences', json=pref)
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertFalse(result.get_json()['preferences']['assignment_enabled'])
        self.admin.post('/api/task', json={'title': '关闭提醒', 'owner_id': self.mid})
        self.assertEqual(self.jobs(), [])

    def test_daily_multiple_groups_only_one_mail_and_self_assignment_none(self):
        self.member.post('/api/daily-projects', json={'title': '自己创建', 'groups': {'mechanical': [self.mid]}})
        self.assertEqual(self.jobs(), [])
        self.admin.post('/api/daily-projects', json={'title': '多组', 'groups': {'mechanical': [self.mid], 'vision': [self.mid]}})
        self.assertEqual(len(self.jobs()), 1)
        state, messages = self.deliver(self.jobs()[0]['id'])
        self.assertEqual(state, 'sent')
        self.assertIn('机械负责人、视觉负责人', messages[0].get_body(preferencelist=('plain',)).get_content())

    def test_expiry_and_revoked_assignment_do_not_send(self):
        for revoked in (False, True):
            task = self.admin.post('/api/task', json={'title': '过时提醒', 'owner_id': self.mid}).get_json()
            job = self.jobs()[0]
            if revoked:
                self.admin.post('/api/task/' + str(task['id']), json={'version': 1, 'owner_id': None})
            state, messages = self.deliver(job['id'], datetime.now(TZ) + timedelta(hours=25) if not revoked else None)
            self.assertEqual(state, 'skipped')
            self.assertEqual(messages, [])

    def test_failed_task_transaction_does_not_queue_and_stale_retry_does_not_duplicate(self):
        bad = self.admin.post('/api/task', json={'title': '失败', 'owner_id': self.mid, 'participant_ids': [2**62]})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.jobs(), [])
        task = self.admin.post('/api/task', json={'title': '未指派'}).get_json()
        path = '/api/task/' + str(task['id'])
        body = {'version': 1, 'owner_id': self.mid}
        self.assertEqual(self.admin.post(path, json=body).status_code, 200)
        self.assertEqual(self.admin.post(path, json=body).status_code, 409)
        self.assertEqual(len(self.jobs()), 1)

    def test_delivery_rechecks_account_and_mail_settings(self):
        for field, value in (('verified', 0), ('paused', 1), ('assignment_enabled', 0), ('generation', 'changed')):
            self.db.execute('UPDATE member_email SET verified=1,paused=0,assignment_enabled=1,generation=? WHERE member_id=?', (uuid4().hex, self.mid)); self.db.commit()
            self.admin.post('/api/task', json={'title': field, 'owner_id': self.mid})
            job = self.jobs()[0]
            self.db.execute(f'UPDATE member_email SET {field}=? WHERE member_id=?', (value, self.mid)); self.db.commit()
            state, messages = self.deliver(job['id'])
            self.assertEqual(state, 'skipped')
            self.assertEqual(messages, [])

    def test_unknown_smtp_result_is_not_automatically_requeued(self):
        self.admin.post('/api/task', json={'title': '结果不明', 'owner_id': self.mid})
        job = dict(self.db.execute('SELECT * FROM mail_jobs WHERE id=?', (self.jobs()[0]['id'],)).fetchone())
        job['attempts'] = 1
        def uncertain(message):
            raise DeliveryError('模拟服务器已接收后断线', 'unknown')
        self.assertEqual(process_job(self.db, job, datetime.now(TZ), uncertain), 'unknown')
        self.assertEqual(self.jobs()[0]['state'], 'unknown')
