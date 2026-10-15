"""Mail tests never contact SMTP; cover binding, content and queue transitions."""
import hashlib
import smtplib
import unittest
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from uuid import uuid4
from test_tasks import app_module
from common import get_db, DB_PATH
from modules.daily_projects import TZ
from modules.mail_service import (verification_token, stale, digest_content, digest_items, enqueue_daily,
                                  claim, process_job, DeliveryError, smtp_send, queue, run_once)


class DailyMailTest(unittest.TestCase):
    def setUp(self):
        self.app=app_module.app;self.ctx=self.app.app_context();self.ctx.push();self.db=get_db()
        self.config=dict(self.app.config['MAIL_SETTINGS'])
        self.app.config['MAIL_SETTINGS'].update(mode='preview',base_url='https://team.example',sender='team@example.com')
        self.admin=self.app.test_client();self.admin.post('/login',json={'username':'admin','password':'ci-test-password-only'})
        name='mail_'+uuid4().hex[:10]
        self.mid=self.admin.post('/api/member',json={'name':name,'username':name,'password':'mail-password'}).get_json()['id']
        self.member=self.app.test_client();self.member.post('/login',json={'username':name,'password':'mail-password'})
        self.at=datetime(2026,10,15,9,tzinfo=TZ)

    def tearDown(self):
        self.app.config['MAIL_SETTINGS']=self.config;self.ctx.pop()

    def bind(self, verified=True):
        generation=uuid4().hex
        self.db.execute('INSERT INTO member_email(member_id,address,verified,generation,requested_at) VALUES(?,?,?,?,?)', (self.mid,'member@example.com',int(verified),generation,self.at.timestamp()))
        self.db.commit();return generation

    def job(self,kind='test'):
        generation=self.bind()
        queue(self.db,self.mid,kind,self.at.date().isoformat(),generation,self.at.timestamp(),uuid4().hex);self.db.commit()
        # Restrict claiming to this fixture by selecting the created job directly.
        row=dict(self.db.execute('SELECT * FROM mail_jobs WHERE member_id=? ORDER BY id DESC LIMIT 1',(self.mid,)).fetchone())
        self.db.execute("UPDATE mail_jobs SET state='sending',attempts=1 WHERE id=?",(row['id'],));self.db.commit()
        return row|{'attempts':1}

    def test_verification_expiry_replay_and_wrong_account(self):
        result=self.member.post('/api/account/email/request',json={'email':'member@example.com'});self.assertEqual(result.status_code,202)
        row=self.db.execute('SELECT * FROM member_email WHERE member_id=?',(self.mid,)).fetchone()
        token=verification_token(row['generation'])
        self.assertNotEqual(token,row['token_hash'])
        self.assertEqual(self.admin.post('/api/account/email/verify',json={'token':token}).status_code,400)
        with patch('modules.email_api.epoch_now',return_value=row['expires_at']+1):
            self.assertEqual(self.member.post('/api/account/email/verify',json={'token':token}).status_code,400)
        self.assertEqual(self.member.post('/api/account/email/verify',json={'token':token}).status_code,200)
        self.assertEqual(self.member.post('/api/account/email/verify',json={'token':token}).status_code,400)

    def test_resend_limit_address_change_and_pause_preserved(self):
        base=self.at.timestamp()
        with patch('modules.email_api.epoch_now',return_value=base):
            self.member.post('/api/account/email/request',json={'email':'first@example.com'})
            old=self.db.execute('SELECT generation FROM member_email WHERE member_id=?',(self.mid,)).fetchone()[0]
            self.assertEqual(self.member.post('/api/account/email/request',json={'email':'second@example.com'}).status_code,429)
        self.admin.post(f'/api/admin/mail/{self.mid}/pause',json={'paused':True})
        for offset in (60,120,180,240):
            with patch('modules.email_api.epoch_now',return_value=base+offset):
                self.assertEqual(self.member.post('/api/account/email/request',json={'email':'second@example.com'}).status_code,202)
        with patch('modules.email_api.epoch_now',return_value=base+300):
            self.assertEqual(self.member.post('/api/account/email/request',json={'email':'second@example.com'}).status_code,429)
        self.assertEqual(self.member.post('/api/account/email/verify',json={'token':verification_token(old)}).status_code,400)
        row=self.db.execute('SELECT * FROM member_email WHERE member_id=?',(self.mid,)).fetchone()
        self.assertEqual(row['address'],'second@example.com');self.assertEqual(row['paused'],1);self.assertEqual(row['verified'],0)

    def test_email_privacy_admin_and_disabled_mode(self):
        self.bind()
        data=self.member.get('/api/data').get_json()
        self.assertNotIn('member@example.com',str(data))
        self.assertEqual(self.member.get('/api/admin/mail').status_code,403)
        self.assertEqual(self.member.post(f'/api/admin/mail/{self.mid}/pause',json={'paused':False}).status_code,403)
        self.app.config['MAIL_SETTINGS']['mode']='off'
        self.assertEqual(self.member.post('/api/account/email/request',json={'email':'new@example.com'}).status_code,503)

    def test_stale_boundary_and_hundred_percent(self):
        exact=(self.at-timedelta(hours=72)).strftime('%Y-%m-%d %H:%M:%S')
        self.assertFalse(stale(exact,1,self.at));self.assertTrue(stale(exact,1,self.at+timedelta(seconds=1)))
        self.assertFalse(stale(exact,100,self.at+timedelta(days=1)))

    def test_personal_digest_groups_roles_escaping_and_dates(self):
        task=self.admin.post('/api/task',json={'title':'<script>目标</script>','owner_id':self.mid,'due_date':'2026-10-15'}).get_json()['id']
        colleague=self.admin.post('/api/member',json={'name':'日报同事','username':'digest_'+uuid4().hex[:10],'password':'mail-password'}).get_json()['id']
        other=self.admin.post('/api/task',json={'title':'参与事项','owner_id':colleague,'participant_ids':[self.mid]}).get_json()['id']
        pid=self.admin.post('/api/daily-projects',json={'title':'日常目标','groups':{'mechanical':[self.mid],'vision':[self.mid],'electrical':[1]}}).get_json()['id']
        old=(self.at-timedelta(days=4)).strftime('%Y-%m-%d %H:%M:%S')
        self.db.execute('UPDATE tasks SET created_at=? WHERE id IN (?,?)',(old,task,other))
        self.db.execute('UPDATE daily_groups SET enabled_at=? WHERE project_id=?',(old,pid));self.db.commit()
        tasks,projects=digest_items(self.db,self.mid,self.at)
        by_title={t['title']:t for t in tasks};self.assertTrue(by_title['<script>目标</script>']['nudge']);self.assertFalse(by_title['参与事项']['nudge'])
        self.assertEqual(by_title['<script>目标</script>']['remaining'],'今天截止')
        self.assertEqual(len(projects),1);self.assertEqual(len(projects[0]['groups']),2)
        text,markup=digest_content(self.db,self.mid,self.at)
        self.assertIn('&lt;script&gt;',markup);self.assertNotIn('<script>',markup);self.assertIn('未设置截止日期',text)
        self.assertNotIn('电控',text)
        self.admin.post(f'/api/daily-projects/{pid}/groups/mechanical/progress',json={'progress':1,'version':1,'project_version':1,'note':'已更新'})
        groups=digest_items(self.db,self.mid,self.at)[1][0]['groups']
        self.assertFalse(groups[0]['nudge']);self.assertTrue(groups[1]['nudge'])

    def test_scheduler_window_dedup_and_no_past_digest(self):
        self.bind()
        enqueue_daily(self.db,self.at-timedelta(minutes=1))
        self.assertEqual(self.db.execute("SELECT count(*) FROM mail_jobs WHERE member_id=?",(self.mid,)).fetchone()[0],0)
        enqueue_daily(self.db,self.at);enqueue_daily(self.db,self.at)
        self.assertEqual(self.db.execute("SELECT count(*) FROM mail_jobs WHERE member_id=?",(self.mid,)).fetchone()[0],1)
        enqueue_daily(self.db,self.at+timedelta(hours=3))
        claim(self.db,self.at+timedelta(hours=3))
        self.assertEqual(self.db.execute('SELECT state FROM mail_jobs WHERE member_id=?',(self.mid,)).fetchone()[0],'skipped')

    def test_claim_is_atomic_and_interrupted_becomes_unknown(self):
        job=self.job();self.db.execute("UPDATE mail_jobs SET claimed_at=? WHERE id=?",(self.at.timestamp()-1900,job['id']));self.db.commit()
        claim(self.db,self.at)
        self.assertEqual(self.db.execute('SELECT state FROM mail_jobs WHERE id=?',(job['id'],)).fetchone()[0],'unknown')
        self.assertNotEqual((claim(self.db,self.at) or {}).get('id'),job['id'])

    def test_empty_digest_is_skipped_without_transport(self):
        job=self.job('daily');sent=[]
        self.assertEqual(process_job(self.db,job,self.at,sent.append),'skipped');self.assertEqual(sent,[])

    def test_transient_retry_permanent_unknown_and_limit(self):
        job=self.job();transport=MagicMock(side_effect=DeliveryError('暂时失败',retry=True))
        self.assertEqual(process_job(self.db,job,self.at,transport),'pending')
        self.assertEqual(self.db.execute('SELECT next_at FROM mail_jobs WHERE id=?',(job['id'],)).fetchone()[0],self.at.timestamp()+900)
        self.assertEqual(process_job(self.db,job|{'attempts':3},self.at,transport),'failed')
        self.assertEqual(process_job(self.db,job,self.at,MagicMock(side_effect=DeliveryError('收件人拒绝'))),'failed')
        self.assertEqual(process_job(self.db,job,self.at,MagicMock(side_effect=DeliveryError('未知','unknown'))),'unknown')

    def test_changed_email_paused_and_inactive_are_checked_at_send(self):
        job=self.job();transport=MagicMock()
        self.db.execute('UPDATE member_email SET paused=1 WHERE member_id=?',(self.mid,));self.db.commit()
        self.assertEqual(process_job(self.db,job,self.at,transport),'skipped')
        self.db.execute('UPDATE member_email SET paused=0,generation=? WHERE member_id=?',('new',self.mid));self.db.commit()
        self.assertEqual(process_job(self.db,job,self.at,transport),'skipped');transport.assert_not_called()
        self.db.execute('UPDATE member_email SET generation=? WHERE member_id=?',(job['generation'],self.mid))
        self.db.execute('UPDATE members SET is_active=0 WHERE id=?',(self.mid,));self.db.commit()
        self.assertEqual(process_job(self.db,job,self.at,transport),'skipped');transport.assert_not_called()

    def test_two_workers_cannot_claim_same_job(self):
        job=self.job()
        # Other test fixtures are made ineligible; only this pending job is due.
        self.db.execute("UPDATE mail_jobs SET state='skipped' WHERE state='pending'")
        self.db.execute("UPDATE mail_jobs SET state='pending',next_at=? WHERE id=?",(self.at.timestamp(),job['id']));self.db.commit()
        barrier=threading.Barrier(2)
        def worker():
            connection=sqlite3.connect(DB_PATH,timeout=15);connection.row_factory=sqlite3.Row
            try:
                barrier.wait();return claim(connection,self.at)
            finally:connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:worker(),range(2)))
        self.assertEqual([r['id'] for r in results if r],[job['id']])

    def test_mail_login_destination_is_local_only(self):
        client=self.app.test_client()
        for destination in ('https://evil.example','//evil.example','/tasks?task=1&next=https://evil.example'):
            result=client.post('/login',json={'username':'admin','password':'ci-test-password-only','next':destination})
            self.assertEqual(result.get_json()['redirect'],'/')
        self.assertEqual(client.post('/login',json={'username':'admin','password':'ci-test-password-only','next':'/daily-projects/1'}).get_json()['redirect'],'/daily-projects/1')

    def test_single_recipient_multipart_and_safe_smtp_failure(self):
        job=self.job();sent=[]
        self.assertEqual(process_job(self.db,job,self.at,sent.append),'sent')
        self.assertEqual(sent[0]['To'],'member@example.com');self.assertIsNone(sent[0]['Cc'])
        self.assertEqual([p.get_content_type() for p in sent[0].iter_parts()],['text/plain','text/html'])
        self.app.config['MAIL_SETTINGS'].update(tls='ssl',username='account',password='private')
        client=MagicMock();client.mail.return_value=(250,b'ok');client.rcpt.return_value=(250,b'ok');client.data.side_effect=TimeoutError()
        with patch('modules.mail_service.smtplib.SMTP_SSL',return_value=client):
            with self.assertRaises(DeliveryError) as error:smtp_send(sent[0])
            self.assertEqual(error.exception.state,'unknown');self.assertNotIn('private',str(error.exception))
        client.data.side_effect=None;client.data.return_value=(250,b'ok');client.quit.side_effect=TimeoutError()
        with patch('modules.mail_service.smtplib.SMTP_SSL',return_value=client):smtp_send(sent[0])

    def test_verification_worker_uses_current_token_and_skips_superseded(self):
        self.member.post('/api/account/email/request',json={'email':'member@example.com'})
        binding=self.db.execute('SELECT * FROM member_email WHERE member_id=?',(self.mid,)).fetchone()
        job=dict(self.db.execute('SELECT * FROM mail_jobs WHERE member_id=? ORDER BY id DESC LIMIT 1',(self.mid,)).fetchone())|{'attempts':1}
        sent=[]
        self.assertEqual(process_job(self.db,job,datetime.now(TZ),sent.append),'sent')
        token=verification_token(binding['generation'])
        self.assertIn('/account/email/verify#token='+token,sent[0].get_body(preferencelist=('plain',)).get_content())
        self.db.execute('UPDATE member_email SET generation=? WHERE member_id=?',('replacement',self.mid));self.db.commit()
        self.assertEqual(process_job(self.db,job,datetime.now(TZ),sent.append),'skipped');self.assertEqual(len(sent),1)

    def test_failure_does_not_stop_next_recipient(self):
        first=self.job()
        # Keep this run isolated from other fixtures' pending messages.
        self.db.execute("UPDATE mail_jobs SET state='skipped' WHERE state='pending'")
        self.db.execute("UPDATE mail_jobs SET state='pending',attempts=0 WHERE id=?",(first['id'],))
        generation=uuid4().hex
        self.db.execute('INSERT INTO member_email(member_id,address,verified,generation,requested_at) VALUES(1,?,1,?,?) ON CONFLICT(member_id) DO UPDATE SET address=excluded.address,verified=1,paused=0,generation=excluded.generation',('admin@example.com',generation,self.at.timestamp()))
        queue(self.db,1,'test',self.at.date().isoformat(),generation,self.at.timestamp(),uuid4().hex);self.db.commit()
        sent=[]
        def transport(message):
            if message['To']=='member@example.com':raise DeliveryError('地址拒绝')
            sent.append(message['To'])
        run_once(self.db,self.at+timedelta(hours=4),transport)
        self.assertIn('admin@example.com',sent)
        self.assertEqual(self.db.execute('SELECT state FROM mail_jobs WHERE id=?',(first['id'],)).fetchone()[0],'failed')
