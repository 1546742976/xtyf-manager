"""Password recovery: enumeration resistance, delivery, expiry and session revocation."""
import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from test_tasks import app_module
from common import DB_PATH
from modules.daily_projects import TZ
from modules.mail_service import process_job, verification_code


class PasswordRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app
        self.old_mail = self.app.config['MAIL_SETTINGS'].copy()
        self.app.config['MAIL_SETTINGS'].update(
            mode='preview', base_url='https://example.invalid', sender='team@example.invalid')
        self.admin = self.app.test_client()
        self.assertTrue(self.admin.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only'
        }).get_json()['ok'])
        self.username = 'recover_' + uuid4().hex[:14]
        self.password = 'test-existing-password'
        created = self.admin.post('/api/member', json={
            'name': '找回测试成员', 'username': self.username, 'password': self.password
        })
        self.assertTrue(created.get_json()['ok'])
        self.mid = created.get_json()['id']
        self.anonymous = self.app.test_client()
        # Isolate the SQLite source throttle from other tests running in one suite.
        self.anonymous.environ_base['REMOTE_ADDR'] = '2001:db8::' + uuid4().hex[:4]
        self.generation = uuid4().hex
        with self.connection() as db:
            db.execute(
                'INSERT INTO member_email(member_id,address,verified,generation,requested_at) '
                'VALUES(?,?,?,?,0)', (self.mid, 'member@example.invalid', 1, self.generation))

    def tearDown(self):
        self.app.config['MAIL_SETTINGS'] = self.old_mail

    @contextmanager
    def connection(self):
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def request_code(self, username=None):
        return self.anonymous.post('/api/password-recovery/request', json={
            'username': self.username if username is None else username
        })

    def confirm(self, code, password='test-replacement-password'):
        return self.anonymous.post('/api/password-recovery/confirm', json={
            'username': self.username, 'code': code, 'new_password': password
        })

    def challenge(self):
        with self.connection() as db:
            row = db.execute('SELECT * FROM password_recovery WHERE member_id=?',
                             (self.mid,)).fetchone()
            return dict(row) if row else None

    def jobs(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM mail_jobs WHERE member_id=? AND kind='password_recovery' ORDER BY id",
                (self.mid,))]

    def code(self):
        # The code derivation reads current_app.secret_key. Keep this helper
        # usable from ordinary test methods without a persistent app context.
        with self.app.app_context():
            return verification_code('recovery:' + self.challenge()['nonce'])

    def recovery_audits(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM security_audit WHERE action='password_recovery.confirm_reset' "
                "AND json_extract(details,'$.member_id')=?", (self.mid,))]

    def test_valid_code_resets_password_and_revokes_other_sessions(self):
        member = self.app.test_client()
        self.assertTrue(member.post('/login', json={
            'username': self.username, 'password': self.password
        }).get_json()['ok'])
        self.assertEqual(member.get('/api/data').status_code, 200)
        response = self.request_code()
        self.assertEqual(response.status_code, 202)
        self.assertNotIn('member@example.invalid', str(response.get_json()))
        self.assertEqual(len(self.jobs()), 1)
        code = self.code()
        with patch('modules.security._limited_audit_event', return_value=False):
            self.assertEqual(self.confirm(code).status_code, 200)
        audits = self.recovery_audits()
        self.assertEqual(len(audits), 1)
        audit = audits[0]
        self.assertIsNone(audit['actor_id'])
        self.assertEqual(audit['target'], '/api/password-recovery/confirm')
        self.assertEqual(audit['outcome'], 'success')
        self.assertEqual(audit['http_status'], 200)
        self.assertEqual(audit['level'], 'critical')
        self.assertTrue(audit['created_at'])
        self.assertTrue(audit['finished_at'])
        # Exact allowlist: no password, code, email address or challenge nonce.
        self.assertEqual(json.loads(audit['details']), {'member_id': self.mid})
        self.assertEqual(member.get('/api/data').status_code, 401)
        self.assertEqual(self.confirm(code).status_code, 400)
        self.assertEqual(len(self.recovery_audits()), 1)
        self.assertEqual(self.anonymous.post('/login', json={
            'username': self.username, 'password': self.password
        }).status_code, 400)
        login = self.anonymous.post('/login', json={
            'username': self.username, 'password': 'test-replacement-password'
        })
        self.assertTrue(login.get_json()['ok'])
        self.assertFalse(login.get_json()['must_change_password'])

    def test_authenticated_recovery_reuses_request_audit(self):
        self.request_code()
        with self.connection() as db:
            before = db.execute(
                "SELECT count(*) FROM security_audit WHERE action='password_recovery.confirm_reset'"
            ).fetchone()[0]
        response = self.admin.post('/api/password-recovery/confirm', json={
            'username': self.username, 'code': self.code(),
            'new_password': 'test-replacement-password',
        })
        self.assertEqual(response.status_code, 200)
        with self.connection() as db:
            after = db.execute(
                "SELECT count(*) FROM security_audit WHERE action='password_recovery.confirm_reset'"
            ).fetchone()[0]
            admin_id = db.execute("SELECT id FROM members WHERE username='admin'").fetchone()[0]
        self.assertEqual(after, before + 1)
        audits = self.recovery_audits()
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]['actor_id'], admin_id)
        self.assertEqual(json.loads(audits[0]['details']), {'member_id': self.mid})

    def test_no_enumeration_for_missing_unverified_or_disabled_accounts(self):
        actual = self.request_code()
        missing = self.request_code('no_such_' + uuid4().hex)
        self.assertEqual(actual.status_code, missing.status_code)
        self.assertEqual(actual.get_json(), missing.get_json())
        with self.connection() as db:
            db.execute('UPDATE member_email SET verified=0 WHERE member_id=?', (self.mid,))
            db.execute('DELETE FROM password_recovery WHERE member_id=?', (self.mid,))
        self.assertEqual(self.request_code().get_json(), actual.get_json())
        self.assertIsNone(self.challenge())
        with self.connection() as db:
            db.execute('UPDATE member_email SET verified=1 WHERE member_id=?', (self.mid,))
            db.execute('UPDATE members SET is_active=0 WHERE id=?', (self.mid,))
        self.assertEqual(self.request_code().get_json(), actual.get_json())
        self.assertIsNone(self.challenge())
        self.assertEqual(len(self.jobs()), 1)

    def test_bad_code_five_attempt_lockout_and_expiry(self):
        self.request_code()
        code = self.code()
        wrong = '000000' if code != '000000' else '000001'
        for _ in range(5):
            response = self.confirm(wrong)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()['error'], '验证码无效或已过期，请重新申请。')
        self.assertEqual(self.challenge()['attempts'], 5)
        self.assertEqual(self.confirm(code).status_code, 400)
        self.assertEqual(self.recovery_audits(), [])
        with self.connection() as db:
            db.execute('UPDATE password_recovery SET attempts=0,expires_at=0 WHERE member_id=?',
                       (self.mid,))
        self.assertEqual(self.confirm(code).status_code, 400)

    def test_rebinding_email_or_admin_reset_invalidates_challenge(self):
        self.request_code()
        code = self.code()
        with self.connection() as db:
            db.execute('UPDATE member_email SET generation=? WHERE member_id=?',
                       (uuid4().hex, self.mid))
        self.assertEqual(self.confirm(code).status_code, 400)
        with self.connection() as db:
            db.execute('UPDATE member_email SET generation=? WHERE member_id=?',
                       (self.generation, self.mid))
        admin_reset = self.admin.post('/api/member/%d/reset' % self.mid, json={})
        self.assertTrue(admin_reset.get_json()['ok'])
        self.assertEqual(self.confirm(code).status_code, 400)

    def test_rate_limit_cooldown_and_account_hourly_cap(self):
        start = 1_800_000_000
        with patch('modules.password_recovery.epoch_now', return_value=start):
            self.assertEqual(self.request_code().status_code, 202)
        with patch('modules.password_recovery.epoch_now', return_value=start + 30):
            self.assertEqual(self.request_code().status_code, 202)
        self.assertEqual(len(self.jobs()), 1)
        for offset in (60, 120, 180, 240):
            with patch('modules.password_recovery.epoch_now', return_value=start + offset):
                self.assertEqual(self.request_code().status_code, 202)
        self.assertEqual(len(self.jobs()), 5)
        # The cooldown has elapsed, but the independent hourly cap still applies.
        with patch('modules.password_recovery.epoch_now', return_value=start + 300):
            self.assertEqual(self.request_code().status_code, 202)
        self.assertEqual(len(self.jobs()), 5)

    def test_successful_reset_does_not_clear_account_request_cooldown(self):
        start = 1_800_000_000
        with patch('modules.password_recovery.epoch_now', return_value=start):
            self.assertEqual(self.request_code().status_code, 202)
        self.assertEqual(len(self.jobs()), 1)
        code = self.code()
        with patch('modules.password_recovery.epoch_now', return_value=start + 10):
            self.assertEqual(self.confirm(code).status_code, 200)
        self.assertIsNone(self.challenge(), 'successful reset consumes the active challenge')
        with patch('modules.password_recovery.epoch_now', return_value=start + 20):
            self.assertEqual(self.request_code().status_code, 202)
        self.assertEqual(len(self.jobs()), 1, 'account cooldown must survive challenge deletion')
        with patch('modules.password_recovery.epoch_now', return_value=start + 60):
            self.assertEqual(self.request_code().status_code, 202)
        self.assertEqual(len(self.jobs()), 2, 'a new request is allowed after 60 seconds')

    def test_source_cap_limits_different_usernames_without_disclosing(self):
        for _ in range(8):
            response = self.request_code('missing_' + uuid4().hex)
            self.assertEqual(response.status_code, 202)
        self.assertEqual(self.request_code().status_code, 202)
        self.assertIsNone(self.challenge())
        self.assertEqual(self.jobs(), [])

    def test_mail_content_and_paused_notification_policy(self):
        with self.connection() as db:
            db.execute('UPDATE member_email SET paused=1 WHERE member_id=?', (self.mid,))
        self.request_code()
        job = self.jobs()[0]
        received = []
        with self.app.app_context(), self.connection() as db:
            state = process_job(db, job | {'attempts': 1}, datetime.now(TZ),
                                transport=received.append)
        self.assertEqual(state, 'sent')
        self.assertEqual(len(received), 1)
        message = received[0]
        self.assertEqual(message['To'], 'member@example.invalid')
        self.assertIn('重置登录密码', message['Subject'])
        self.assertIn(self.code(), message.get_body(preferencelist=('plain',)).get_content())
        self.assertIn('15 分钟', message.get_body(preferencelist=('html',)).get_content())

    def test_invalid_form_and_reusing_current_password(self):
        self.request_code()
        code = self.code()
        self.assertEqual(self.confirm('invalid').status_code, 400)
        self.assertEqual(self.confirm(code, 'short').status_code, 400)
        self.assertEqual(self.challenge()['attempts'], 0)
        self.assertEqual(self.confirm(code, self.password).status_code, 400)
        self.assertEqual(self.confirm(code).status_code, 200)

    def test_login_contains_recovery_flow(self):
        page = self.anonymous.get('/login')
        self.assertEqual(page.status_code, 200)
        for element in (b'id="open-recovery"', b'id="recovery-request-form"',
                        b'id="recovery-confirm-form"'):
            self.assertIn(element, page.data)


if __name__ == '__main__':
    unittest.main()
