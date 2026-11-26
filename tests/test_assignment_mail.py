"""Assignment delivery through task APIs and the existing mail worker seam."""
import unittest
import sqlite3
from unittest.mock import patch
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

    def test_assignments_aggregate_with_fixed_ten_minute_deadline(self):
        from modules.mail_service import claim
        now = datetime.now(TZ)
        with patch('modules.task_assignment_mail.datetime') as clock:
            clock.now.return_value = now
            self.admin.post('/api/task', json={'title': '第一项', 'owner_id': self.mid})
            clock.now.return_value = now + timedelta(minutes=9)
            self.admin.post('/api/task', json={'title': '第二项', 'owner_id': self.mid})
        jobs = self.db.execute("SELECT * FROM mail_jobs WHERE member_id=? AND kind='assignment'", (self.mid,)).fetchall()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['next_at'], now.timestamp() + 600)
        claim(self.db, now)
        self.assertEqual(self.db.execute('SELECT state FROM mail_jobs WHERE id=?', (jobs[0]['id'],)).fetchone()[0], 'pending')
        state, messages = self.deliver(jobs[0]['id'], now + timedelta(minutes=10))
        self.assertEqual(state, 'sent')
        text = messages[0].get_body(preferencelist=('plain',)).get_content()
        self.assertIn('第一项', text)
        self.assertIn('第二项', text)

    def test_aggregation_filters_revoked_tasks_and_starts_new_window(self):
        now = datetime.now(TZ)
        with patch('modules.task_assignment_mail.datetime') as clock:
            clock.now.return_value = now
            first = self.admin.post('/api/task', json={'title': '后来取消', 'owner_id': self.mid}).get_json()['id']
            self.admin.post('/api/task', json={'title': '仍需执行', 'owner_id': self.mid})
            clock.now.return_value = now + timedelta(minutes=10)
            self.admin.post('/api/task', json={'title': '下一窗口', 'owner_id': self.mid})
        self.assertEqual(len(self.jobs()), 2)
        self.admin.post(f'/api/task/{first}', json={'version': 1, 'status': '已取消', 'note': '测试取消'})
        oldest = min(job['id'] for job in self.jobs())
        state, messages = self.deliver(oldest, now + timedelta(minutes=10))
        self.assertEqual(state, 'sent')
        text = messages[0].get_body(preferencelist=('plain',)).get_content()
        self.assertIn('仍需执行', text)
        self.assertNotIn('后来取消', text)
        self.assertNotIn('下一窗口', text)

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

    def test_enqueue_database_failure_rolls_back_task_and_mail_together(self):
        from modules.task_assignment_mail import enqueue_assignments
        def failing_queue(*args, **kwargs):
            enqueue_assignments(*args, **kwargs)
            raise sqlite3.OperationalError('synthetic transaction failure')
        title = 'rollback_' + uuid4().hex
        with patch('modules.task_mutations.enqueue_assignments', side_effect=failing_queue):
            response = self.admin.post('/api/task', json={'title': title, 'owner_id': self.mid})
        self.assertEqual(response.status_code, 500)
        self.assertIsNone(self.db.execute('SELECT id FROM tasks WHERE title=?', (title,)).fetchone())
        self.assertEqual(self.jobs(), [])

    def test_finished_tasks_and_inactive_recipients_are_skipped(self):
        for status in ('已完成', '已取消'):
            task = self.admin.post('/api/task', json={'title': status, 'owner_id': self.mid}).get_json()
            self.admin.post('/api/task/' + str(task['id']), json={'version': 1, 'status': status, 'note': '终态原因'})
            state, messages = self.deliver(self.jobs()[0]['id'])
            self.assertEqual((state, messages), ('skipped', []))
        self.admin.post('/api/task', json={'title': '已停用收件人', 'owner_id': self.mid})
        self.db.execute('UPDATE members SET is_active=0 WHERE id=?', (self.mid,)); self.db.commit()
        state, messages = self.deliver(self.jobs()[0]['id'])
        self.assertEqual((state, messages), ('skipped', []))
