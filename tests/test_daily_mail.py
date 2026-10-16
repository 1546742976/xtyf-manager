"""Mail tests never contact SMTP; cover binding, content and queue transitions."""
import hashlib
import smtplib
import unittest
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from uuid import uuid4
from test_tasks import app_module
from common import get_db, DB_PATH
from modules.daily_projects import TZ
from modules.mail_service import (verification_code, verification_digest, stale, deadline, daily_window, digest_content, digest_items, enqueue_daily,
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
        self.at=datetime(2026,10,15,22,tzinfo=TZ)

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

    def colleague(self):
        name='digest_'+uuid4().hex[:10]
        return self.admin.post('/api/member',json={'name':name,'username':name,'password':'mail-password'}).get_json()['id']

    def task(self, **fields):
        result=self.admin.post('/api/task',json={'title':'团队任务 '+uuid4().hex[:6],**fields})
        self.assertEqual(result.status_code,200,result.get_json())
        return result.get_json()['id']

    def project(self, **fields):
        result=self.admin.post('/api/daily-projects',json={'title':'团队目标 '+uuid4().hex[:6],'groups':{'mechanical':[self.mid]},**fields})
        self.assertEqual(result.status_code,200,result.get_json())
        return result.get_json()['id']

    def queue_job(self, kind, at, generation, day=None):
        queue(self.db,self.mid,kind,day or at.date().isoformat(),generation,at.timestamp(),uuid4().hex)
        self.db.commit()
        return dict(self.db.execute('SELECT * FROM mail_jobs WHERE member_id=? ORDER BY id DESC LIMIT 1',(self.mid,)).fetchone())

    def test_morning_personal_scope_roles_and_cross_group_aggregation(self):
        morning=self.at.replace(hour=8);colleague=self.colleague()
        owned=self.task(title='本人负责早报任务',owner_id=self.mid,due_date='2026-10-15')
        participated=self.task(title='本人参与早报任务',owner_id=colleague,participant_ids=[self.mid])
        self.task(title='仅其他成员负责的早报任务',owner_id=colleague)
        self.task(title='未分配的早报任务')
        pid=self.project(title='跨组个人早报项目',groups={'mechanical':[self.mid,colleague],'vision':[self.mid],'electrical':[colleague]})
        second=self.project(title='另一个个人早报项目',groups={'electrical':[self.mid]})
        self.project(title='仅其他成员的早报项目',groups={'mechanical':[colleague]})
        tasks,projects=digest_items(self.db,self.mid,morning,kind='morning')
        self.assertEqual({task['id'] for task in tasks},{owned,participated})
        by_id={task['id']:task for task in tasks}
        self.assertEqual(by_id[owned]['role'],'负责人');self.assertEqual(by_id[participated]['role'],'参与者')
        self.assertEqual(by_id[owned]['remaining'],'今天截止')
        self.assertEqual({project['id'] for project in projects},{pid,second})
        selected=next(project for project in projects if project['id']==pid)
        self.assertEqual({group['label'] for group in selected['groups']},{'机械','视觉'})
        self.assertEqual(sum(project['id']==pid for project in projects),1)
        content=digest_content(self.db,self.mid,morning,kind='morning')
        for body in content:
            for title in ('本人负责早报任务','本人参与早报任务','跨组个人早报项目','另一个个人早报项目'):
                self.assertIn(title,body)
            for title in ('仅其他成员负责的早报任务','未分配的早报任务','仅其他成员的早报项目'):
                self.assertNotIn(title,body)

    def test_morning_excludes_completed_cancelled_deleted_and_disabled_items(self):
        morning=self.at.replace(hour=8);expected_tasks=set();expected_projects=set()
        for status,deleted,include in (('未开始',False,True),('进行中',False,True),
                                      ('已完成',False,False),('已取消',False,False),('进行中',True,False)):
            tid=self.task(owner_id=self.mid);pid=self.project()
            completion='2026-10-15 07:00:00' if status=='已完成' else None
            removed='2026-10-15 07:00:00' if deleted else None
            self.db.execute('UPDATE tasks SET status=?,completed_at=?,deleted_at=? WHERE id=?',(status,completion,removed,tid))
            self.db.execute('UPDATE daily_projects SET status=?,completed_at=?,deleted_at=? WHERE id=?',(status,completion,removed,pid))
            self.db.commit()
            if include:expected_tasks.add(tid);expected_projects.add(pid)
        disabled=self.project(groups={'mechanical':[self.mid]})
        self.db.execute('UPDATE daily_groups SET enabled=0 WHERE project_id=?',(disabled,));self.db.commit()
        tasks,projects=digest_items(self.db,self.mid,morning,kind='morning')
        self.assertEqual({task['id'] for task in tasks},expected_tasks)
        self.assertEqual({project['id'] for project in projects},expected_projects)

    def test_morning_updates_escaping_and_responsibility_only_nudges(self):
        morning=self.at.replace(hour=8);colleague=self.colleague()
        exact=(morning-timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')
        old=(morning-timedelta(hours=49)).strftime('%Y-%m-%d %H:%M:%S')
        recent=(morning-timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
        owned=self.task(title='<script>本人旧任务</script>',owner_id=self.mid)
        boundary=self.task(owner_id=self.mid);full=self.task(owner_id=self.mid)
        participant=self.task(owner_id=colleague,participant_ids=[self.mid])
        updated=self.task(title='本人近期更新任务',owner_id=self.mid)
        response=self.admin.post(f'/api/task/{updated}/progress',data={'progress':'10','note':'早报不应公开的完整日志','version':'1'})
        self.assertEqual(response.status_code,200,response.get_json())
        pid=self.project(groups={'mechanical':[self.mid],'vision':[self.mid],'electrical':[self.mid]})
        self.db.execute('UPDATE tasks SET created_at=? WHERE id IN (?,?,?,?,?)',(old,owned,boundary,full,participant,updated))
        self.db.execute('UPDATE tasks SET created_at=? WHERE id=?',(exact,boundary))
        self.db.execute('UPDATE tasks SET progress=100 WHERE id=?',(full,))
        self.db.execute('UPDATE task_progress_updates SET created_at=? WHERE task_id=?',(recent,updated))
        self.db.execute('UPDATE daily_groups SET enabled_at=? WHERE project_id=?',(old,pid))
        self.db.execute('UPDATE daily_groups SET enabled_at=? WHERE project_id=? AND stage=?',(exact,pid,'mechanical'))
        self.db.execute('UPDATE daily_groups SET progress=100 WHERE project_id=? AND stage=?',(pid,'electrical'));self.db.commit()
        tasks,projects=digest_items(self.db,self.mid,morning,kind='morning');by_id={task['id']:task for task in tasks}
        self.assertTrue(by_id[owned]['nudge'])
        for tid in (boundary,full,participant,updated):self.assertFalse(by_id[tid]['nudge'])
        self.assertEqual(by_id[updated]['last_progress'],recent)
        self.assertEqual(by_id[updated]['reminder_baseline'],recent)
        self.assertIsNone(by_id[owned]['last_progress']);self.assertEqual(by_id[owned]['reminder_baseline'],old)
        groups={group['label']:group for project in projects if project['id']==pid for group in project['groups']}
        self.assertFalse(groups['机械']['nudge']);self.assertTrue(groups['视觉']['nudge']);self.assertFalse(groups['电控']['nudge'])
        text,markup=digest_content(self.db,self.mid,morning,kind='morning')
        self.assertIn('&lt;script&gt;',markup);self.assertNotIn('<script>',markup)
        for body in (text,markup):
            self.assertIn('负责人',body);self.assertIn('参与者',body);self.assertIn(recent,body)
            self.assertIn('暂无进度更新',body);self.assertIn('请更新进度：',body)
            self.assertNotIn('早报不应公开的完整日志',body)

    def test_morning_and_evening_have_independent_same_day_dedupe(self):
        self.bind();self.task(owner_id=self.mid)
        morning=self.at.replace(hour=8)
        for at in (morning,morning,self.at,self.at):enqueue_daily(self.db,at)
        rows=self.db.execute('SELECT kind,day,dedupe_key FROM mail_jobs WHERE member_id=? ORDER BY kind',(self.mid,)).fetchall()
        self.assertEqual([(row['kind'],row['day'],row['dedupe_key']) for row in rows],
                         [('daily','2026-10-15',f'daily:2026-10-15:{self.mid}'),
                          ('morning','2026-10-15',f'morning:2026-10-15:{self.mid}')])
        # Rebinding or restarting later on the same day must not duplicate either brief.
        self.db.execute('UPDATE member_email SET generation=? WHERE member_id=?',(uuid4().hex,self.mid));self.db.commit()
        enqueue_daily(self.db,morning+timedelta(hours=1));enqueue_daily(self.db,self.at+timedelta(hours=1))
        self.assertEqual(self.db.execute('SELECT count(*) FROM mail_jobs WHERE member_id=?',(self.mid,)).fetchone()[0],2)

    def test_morning_and_evening_beijing_windows_include_weekends(self):
        for hour,minute,second,morning,evening in ((7,59,59,False,False),(8,0,0,True,False),
                (11,59,59,True,False),(12,0,0,False,False),(21,59,59,False,False),
                (22,0,0,False,True),(23,59,59,False,True),(0,0,0,False,False)):
            at=self.at.replace(hour=hour,minute=minute,second=second)
            with self.subTest(at=at):
                self.assertEqual(daily_window(at,kind='morning'),morning)
                self.assertEqual(daily_window(at.astimezone(timezone.utc),kind='morning'),morning)
                self.assertEqual(daily_window(at),evening)
        self.assertFalse(daily_window(self.at.replace(hour=8),'2026-10-14',kind='morning'))
        self.bind();self.task(owner_id=self.mid)
        saturday=datetime(2026,10,17,0,tzinfo=timezone.utc)
        enqueue_daily(self.db,saturday)
        row=self.db.execute('SELECT kind,day FROM mail_jobs WHERE member_id=?',(self.mid,)).fetchone()
        self.assertEqual((row['kind'],row['day']),('morning','2026-10-17'))

    def test_scheduler_does_not_catch_up_missed_morning_or_previous_day(self):
        self.bind();self.task(owner_id=self.mid)
        for at in (self.at.replace(hour=7,minute=59),self.at.replace(hour=12),self.at.replace(hour=21)):
            enqueue_daily(self.db,at)
        self.assertEqual(self.db.execute('SELECT count(*) FROM mail_jobs WHERE member_id=?',(self.mid,)).fetchone()[0],0)
        enqueue_daily(self.db,self.at)
        enqueue_daily(self.db,(self.at+timedelta(days=1)).replace(hour=8))
        rows=self.db.execute('SELECT kind,day FROM mail_jobs WHERE member_id=? ORDER BY id',(self.mid,)).fetchall()
        self.assertEqual([(row['kind'],row['day']) for row in rows],[('daily','2026-10-15'),('morning','2026-10-16')])

    def test_morning_claim_skips_jobs_outside_their_day_and_window(self):
        generation=self.bind()
        self.db.execute("UPDATE mail_jobs SET state='skipped' WHERE state='pending'");self.db.commit()
        for at,day in ((self.at.replace(hour=7,minute=59),'2026-10-15'),
                       (self.at.replace(hour=12),'2026-10-15'),(self.at,'2026-10-15'),
                       (self.at.replace(hour=8),'2026-10-14')):
            with self.subTest(at=at,day=day):
                job=self.queue_job('morning',at,generation,day)
                self.assertIsNone(claim(self.db,at))
                row=self.db.execute('SELECT state,attempts FROM mail_jobs WHERE id=?',(job['id'],)).fetchone()
                self.assertEqual((row['state'],row['attempts']),('skipped',0))

    def test_empty_morning_skips_but_evening_still_sends_team_content(self):
        colleague=self.colleague();self.task(title='其他同事仍需处理的任务',owner_id=colleague)
        job=self.job('morning');transport=MagicMock();morning=self.at.replace(hour=8)
        self.assertIsNone(digest_content(self.db,self.mid,morning,kind='morning'))
        self.assertEqual(process_job(self.db,job,morning,transport),'skipped');transport.assert_not_called()
        evening=self.queue_job('daily',self.at,job['generation'])|{'attempts':1}
        sent=[];self.assertEqual(process_job(self.db,evening,self.at,sent.append),'sent')
        self.assertEqual(sent[0]['Subject'],'巡天御风｜团队项目晚报｜2026-10-15')
        self.assertIn('其他同事仍需处理的任务',sent[0].get_body(preferencelist=('plain',)).get_content())

    def test_morning_send_subject_single_recipient_and_window_recheck(self):
        self.task(title='早报收件检查',owner_id=self.mid)
        job=self.job('morning');morning=self.at.replace(hour=8);sent=[]
        self.assertEqual(process_job(self.db,job,morning,sent.append),'sent')
        message=sent[0]
        self.assertEqual(message['Subject'],'巡天御风｜个人任务早报｜2026-10-15')
        self.assertEqual(message['To'],'member@example.com');self.assertIsNone(message['Cc']);self.assertIsNone(message['Bcc'])
        self.assertEqual([part.get_content_type() for part in message.iter_parts()],['text/plain','text/html'])
        transport=MagicMock()
        for at in (morning-timedelta(seconds=1),morning.replace(hour=12),self.at,morning+timedelta(days=1)):
            with self.subTest(at=at):self.assertEqual(process_job(self.db,job,at,transport),'skipped')
        transport.assert_not_called()

    def test_morning_retries_after_fifteen_minutes_at_most_three_attempts(self):
        self.task(owner_id=self.mid);job=self.job('morning');morning=self.at.replace(hour=8)
        self.db.execute("UPDATE mail_jobs SET state='skipped' WHERE state='pending'");self.db.commit()
        transport=MagicMock(side_effect=DeliveryError('临时失败',retry=True))
        self.assertEqual(process_job(self.db,job,morning,transport),'pending')
        self.assertIsNone(claim(self.db,morning+timedelta(minutes=15,seconds=-1)))
        second=claim(self.db,morning+timedelta(minutes=15))
        self.assertEqual((second['id'],second['attempts']),(job['id'],2))
        self.assertEqual(process_job(self.db,second,morning+timedelta(minutes=15),transport),'pending')
        third=claim(self.db,morning+timedelta(minutes=30))
        self.assertEqual((third['id'],third['attempts']),(job['id'],3))
        self.assertEqual(process_job(self.db,third,morning+timedelta(minutes=30),transport),'failed')
        self.assertIsNone(claim(self.db,morning+timedelta(minutes=45)));self.assertEqual(transport.call_count,3)

    def test_morning_retry_cannot_cross_noon_and_unknown_is_not_retried(self):
        self.task(owner_id=self.mid);job=self.job('morning')
        transport=MagicMock(side_effect=DeliveryError('临时失败',retry=True));late=self.at.replace(hour=11,minute=44)
        self.assertEqual(process_job(self.db,job,late,transport),'pending')
        row=self.db.execute('SELECT next_at,finished_at FROM mail_jobs WHERE id=?',(job['id'],)).fetchone()
        self.assertEqual(row['next_at'],late.timestamp()+900);self.assertIsNone(row['finished_at'])
        self.assertEqual(process_job(self.db,job,late+timedelta(minutes=1),transport),'failed')
        self.assertEqual(process_job(self.db,job,self.at.replace(hour=8),
                         MagicMock(side_effect=DeliveryError('提交结果未知','unknown',retry=True))),'unknown')

    def test_morning_rechecks_binding_pause_and_member_state_before_send(self):
        self.task(owner_id=self.mid);job=self.job('morning');morning=self.at.replace(hour=8);transport=MagicMock()
        changes=(('member_email','verified',0),('member_email','paused',1),
                 ('member_email','generation','replacement'),('members','is_active',0),
                 ('members','deleted_at','2026-10-15 07:00:00'))
        for table,field,value in changes:
            with self.subTest(field=field):
                self.db.execute('UPDATE member_email SET verified=1,paused=0,generation=? WHERE member_id=?',(job['generation'],self.mid))
                self.db.execute('UPDATE members SET is_active=1,deleted_at=NULL WHERE id=?',(self.mid,))
                key='member_id' if table=='member_email' else 'id'
                self.db.execute(f'UPDATE {table} SET {field}=? WHERE {key}=?',(value,self.mid));self.db.commit()
                self.assertEqual(process_job(self.db,job,morning,transport),'skipped')
        transport.assert_not_called()

    def test_verification_and_test_mail_take_priority_over_digest(self):
        self.db.execute("UPDATE mail_jobs SET state='skipped' WHERE state='pending'");self.db.commit()
        generation=self.bind()
        for kind in ('daily','test','verify'):
            queue(self.db,self.mid,kind,self.at.date().isoformat(),generation,self.at.timestamp(),uuid4().hex)
        self.db.commit()
        self.assertEqual([claim(self.db,self.at)['kind'] for _ in range(3)],['verify','test','daily'])

    def test_verification_expiry_replay_and_wrong_account(self):
        result=self.member.post('/api/account/email/request',json={'email':'member@example.com'});self.assertEqual(result.status_code,202)
        row=self.db.execute('SELECT * FROM member_email WHERE member_id=?',(self.mid,)).fetchone()
        code=verification_code(row['generation'])
        self.assertRegex(code,r'^[0-9]{6}$')
        self.assertEqual(row['token_hash'],verification_digest(row['generation'],code))
        self.assertNotEqual(code,row['token_hash'])
        self.assertEqual(self.admin.post('/api/account/email/verify',json={'code':code}).status_code,400)
        with patch('modules.email_api.epoch_now',return_value=row['expires_at']+1):
            self.assertEqual(self.member.post('/api/account/email/verify',json={'code':code}).status_code,400)
        self.assertEqual(self.member.post('/api/account/email/verify',json={'token':'a'*64}).status_code,400)
        self.assertEqual(self.member.post('/api/account/email/verify',json={'code':code}).status_code,200)
        self.assertEqual(self.member.post('/api/account/email/verify',json={'code':code}).status_code,400)
        still=self.db.execute('SELECT verified,token_hash FROM member_email WHERE member_id=?',(self.mid,)).fetchone()
        self.assertEqual(still['verified'],1);self.assertIsNone(still['token_hash'])
        self.assertEqual(self.member.post('/api/account/email/request',json={'email':'member@example.com'}).status_code,200)
        self.assertEqual(self.member.get('/account/email/verify').status_code,302)

    def test_wrong_codes_lock_after_five_attempts_and_keep_verifier_private(self):
        self.member.post('/api/account/email/request',json={'email':'member@example.com'})
        row=self.db.execute('SELECT * FROM member_email WHERE member_id=?',(self.mid,)).fetchone()
        code=verification_code(row['generation'])
        wrong='000000' if code!='000000' else '000001'
        state=self.member.get('/api/account/email').get_json()['email']
        self.assertTrue(state['pending']);self.assertNotIn('token_hash',state);self.assertNotIn('verify_attempts',state)
        for attempt in range(5):
            response=self.member.post('/api/account/email/verify',json={'code':wrong})
            self.assertEqual(response.status_code,400,response.get_json())
        current=self.db.execute('SELECT token_hash,verify_attempts,verified FROM member_email WHERE member_id=?',(self.mid,)).fetchone()
        self.assertIsNone(current['token_hash']);self.assertEqual(current['verify_attempts'],5);self.assertFalse(current['verified'])
        self.assertFalse(self.member.get('/api/account/email').get_json()['email']['pending'])
        self.assertEqual(self.member.post('/api/account/email/verify',json={'code':code}).status_code,400)
        with patch('modules.email_api.epoch_now',return_value=row['requested_at']+60):
            self.assertEqual(self.member.post('/api/account/email/request',json={'email':'member@example.com'}).status_code,202)
        self.assertEqual(self.db.execute('SELECT verify_attempts FROM member_email WHERE member_id=?',(self.mid,)).fetchone()[0],0)

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
        self.assertEqual(self.member.post('/api/account/email/verify',json={'code':verification_code(old)}).status_code,400)
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
        exact=(self.at-timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')
        self.assertFalse(stale(exact,1,self.at));self.assertTrue(stale(exact,1,self.at+timedelta(seconds=1)))
        self.assertFalse(stale(exact,100,self.at+timedelta(days=1)))

    def test_team_digest_groups_roles_escaping_and_dates(self):
        task=self.admin.post('/api/task',json={'title':'<script>目标</script>','owner_id':self.mid,'due_date':'2026-10-15'}).get_json()['id']
        colleague=self.admin.post('/api/member',json={'name':'日报同事','username':'digest_'+uuid4().hex[:10],'password':'mail-password'}).get_json()['id']
        other=self.admin.post('/api/task',json={'title':'参与事项','owner_id':colleague,'participant_ids':[self.mid]}).get_json()['id']
        pid=self.project(title='日常目标',groups={'mechanical':[self.mid],'vision':[self.mid],'electrical':[colleague]})
        old=(self.at-timedelta(days=4)).strftime('%Y-%m-%d %H:%M:%S')
        self.db.execute('UPDATE tasks SET created_at=? WHERE id IN (?,?)',(old,task,other))
        self.db.execute('UPDATE daily_groups SET enabled_at=? WHERE project_id=?',(old,pid));self.db.commit()
        tasks,projects=digest_items(self.db,self.mid,self.at)
        by_id={t['id']:t for t in tasks};self.assertTrue(by_id[task]['nudge']);self.assertFalse(by_id[other]['nudge'])
        self.assertEqual(by_id[task]['remaining'],'今天截止')
        project=next(p for p in projects if p['id']==pid)
        self.assertEqual(len(project['groups']),3)
        text,markup=digest_content(self.db,self.mid,self.at)
        self.assertIn('&lt;script&gt;',markup);self.assertNotIn('<script>',markup);self.assertIn('未设置截止日期',text)
        self.assertIn('电控 0%',text)
        self.admin.post(f'/api/daily-projects/{pid}/groups/mechanical/progress',json={'progress':1,'version':1,'project_version':1,'note':'已更新'})
        groups=next(p for p in digest_items(self.db,self.mid,self.at)[1] if p['id']==pid)['groups']
        by_label={g['label']:g for g in groups}
        self.assertFalse(by_label['机械']['nudge']);self.assertFalse(by_label['电控']['nudge']);self.assertTrue(by_label['视觉']['nudge'])

    def test_scheduler_window_dedup_and_no_past_digest(self):
        self.bind()
        enqueue_daily(self.db,self.at-timedelta(minutes=1))
        self.assertEqual(self.db.execute("SELECT count(*) FROM mail_jobs WHERE member_id=?",(self.mid,)).fetchone()[0],0)
        enqueue_daily(self.db,self.at);enqueue_daily(self.db,self.at)
        self.assertEqual(self.db.execute("SELECT count(*) FROM mail_jobs WHERE member_id=?",(self.mid,)).fetchone()[0],1)
        enqueue_daily(self.db,self.at+timedelta(hours=3))
        claim(self.db,self.at+timedelta(hours=3))
        self.assertEqual(self.db.execute('SELECT state FROM mail_jobs WHERE member_id=?',(self.mid,)).fetchone()[0],'skipped')

    def test_scheduler_excludes_unverified_paused_inactive_and_deleted_accounts(self):
        self.bind();excluded=[]
        for field in ('verified','paused','is_active','deleted_at'):
            mid=self.colleague();excluded.append(mid)
            self.db.execute('INSERT INTO member_email(member_id,address,verified,generation,requested_at) VALUES(?,?,1,?,?)',
                            (mid,f'member-{mid}@example.com',uuid4().hex,self.at.timestamp()))
            if field=='verified':self.db.execute('UPDATE member_email SET verified=0 WHERE member_id=?',(mid,))
            elif field=='paused':self.db.execute('UPDATE member_email SET paused=1 WHERE member_id=?',(mid,))
            elif field=='is_active':self.db.execute('UPDATE members SET is_active=0 WHERE id=?',(mid,))
            else:self.db.execute('UPDATE members SET deleted_at=? WHERE id=?',('2026-10-15 10:00:00',mid))
            self.db.commit()
        self.db.commit();enqueue_daily(self.db,self.at)
        queued={r['member_id'] for r in self.db.execute("SELECT member_id FROM mail_jobs WHERE kind='daily' AND day=?",(self.at.date().isoformat(),))}
        self.assertIn(self.mid,queued);self.assertFalse(queued.intersection(excluded))

    def test_claim_is_atomic_and_interrupted_becomes_unknown(self):
        job=self.job();self.db.execute("UPDATE mail_jobs SET claimed_at=? WHERE id=?",(self.at.timestamp()-1900,job['id']));self.db.commit()
        claim(self.db,self.at)
        self.assertEqual(self.db.execute('SELECT state FROM mail_jobs WHERE id=?',(job['id'],)).fetchone()[0],'unknown')
        self.assertNotEqual((claim(self.db,self.at) or {}).get('id'),job['id'])

    def test_empty_digest_is_skipped_without_transport(self):
        job=self.job('daily');sent=[]
        # Shared integration fixtures contain other team tasks; empty means both team lists are empty.
        with patch('modules.mail_service.digest_items',return_value=([],[])):
            self.assertIsNone(digest_content(self.db,self.mid,self.at))
            self.assertEqual(process_job(self.db,job,self.at,sent.append),'skipped')
        self.assertEqual(sent,[])

    def test_team_scope_includes_only_unfinished_and_completed_today(self):
        cases=[('进行中',None,False,True),('未开始',None,False,True),
               ('已完成','2026-10-15 00:00:00',False,True),
               ('已完成','2026-10-15 23:59:59',False,True),
               ('已完成','2026-10-14 23:59:59',False,False),
               ('已完成','2026-10-16 00:00:00',False,False),
               ('已完成',None,False,False),('已取消','2026-10-15 12:00:00',False,False),
               ('进行中',None,True,False)]
        fixture_tasks=set();fixture_projects=set();expected_tasks=set();expected_projects=set()
        for status,completion,deleted,include in cases:
            tid=self.task(owner_id=self.mid);pid=self.project()
            values=(status,completion,'2026-10-15 12:00:00' if deleted else None,'2026-10-15 20:00:00')
            # A title/date edit today must not make an older completion look like today's completion.
            self.db.execute('UPDATE tasks SET status=?,completed_at=?,deleted_at=?,updated_at=?,created_at=? WHERE id=?',(*values,'2026-10-01 00:00:00',tid))
            self.db.execute('UPDATE daily_projects SET status=?,completed_at=?,deleted_at=?,updated_at=? WHERE id=?',(*values,pid))
            self.db.execute('UPDATE daily_groups SET enabled_at=? WHERE project_id=?',('2026-10-01 00:00:00',pid))
            self.db.commit();fixture_tasks.add(tid);fixture_projects.add(pid)
            if include:expected_tasks.add(tid);expected_projects.add(pid)
        tasks,projects=digest_items(self.db,self.mid,self.at)
        self.assertEqual({t['id'] for t in tasks}&fixture_tasks,expected_tasks)
        self.assertEqual({p['id'] for p in projects}&fixture_projects,expected_projects)
        self.assertTrue(all(not t['nudge'] for t in tasks if t['status']=='已完成'))
        self.assertTrue(all(not g['nudge'] for p in projects if p['status']=='已完成' for g in p['groups']))

    def test_no_personal_assignment_still_receives_team_brief(self):
        owner=self.colleague()
        self.task(title='其他成员负责的底盘',owner_id=owner)
        self.task(title='尚未分配的任务')
        self.project(title='其他成员的项目',groups={'mechanical':[owner]})
        job=self.job('daily');sent=[]
        self.assertEqual(process_job(self.db,job,self.at,sent.append),'sent')
        message=sent[0];text=message.get_body(preferencelist=('plain',)).get_content()
        self.assertEqual(message['Subject'],'巡天御风｜团队项目晚报｜2026-10-15')
        self.assertEqual(message['To'],'member@example.com');self.assertIsNone(message['Cc']);self.assertIsNone(message['Bcc'])
        for title in ('其他成员负责的底盘','尚未分配的任务','其他成员的项目'):
            self.assertIn(title,text)
        self.assertNotIn('请更新进度：',text)

    def test_same_team_items_but_nudges_follow_recipient_responsibility(self):
        colleague=self.colleague()
        owned=self.task(title='本人任务',owner_id=self.mid)
        participated=self.task(title='仅参与任务',owner_id=colleague,participant_ids=[self.mid])
        pid=self.project(groups={'mechanical':[self.mid,colleague],'vision':[colleague]})
        old=(self.at-timedelta(hours=49)).strftime('%Y-%m-%d %H:%M:%S')
        self.db.execute('UPDATE tasks SET created_at=? WHERE id IN (?,?)',(old,owned,participated))
        self.db.execute('UPDATE daily_groups SET enabled_at=? WHERE project_id=?',(old,pid));self.db.commit()
        mine=digest_items(self.db,self.mid,self.at);theirs=digest_items(self.db,colleague,self.at)
        self.assertEqual([t['id'] for t in mine[0]],[t['id'] for t in theirs[0]])
        self.assertEqual([p['id'] for p in mine[1]],[p['id'] for p in theirs[1]])
        for items,owner,participant in ((mine,owned,participated),(theirs,participated,owned)):
            tasks={t['id']:t for t in items[0]}
            self.assertTrue(tasks[owner]['nudge']);self.assertFalse(tasks[participant]['nudge'])
        def groups(member):
            return {g['label']:g for p in digest_items(self.db,member,self.at)[1] if p['id']==pid for g in p['groups']}
        self.assertTrue(groups(self.mid)['机械']['nudge']);self.assertFalse(groups(self.mid)['视觉']['nudge'])
        self.assertTrue(groups(colleague)['机械']['nudge']);self.assertTrue(groups(colleague)['视觉']['nudge'])
        with patch('modules.daily_projects.stamp',return_value=self.at.strftime('%Y-%m-%d %H:%M:%S')):
            response=self.member.post(f'/api/daily-projects/{pid}/groups/mechanical/progress',json={'progress':20,'note':'共用组更新','version':1,'project_version':1})
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertFalse(groups(self.mid)['机械']['nudge']);self.assertFalse(groups(colleague)['机械']['nudge'])
        self.assertTrue(groups(colleague)['视觉']['nudge'])

    def test_each_team_brief_has_only_its_own_recipient_and_no_private_logs(self):
        colleague=self.colleague()
        tid=self.task(title='收件隔离检查',owner_id=self.mid)
        response=self.admin.post(f'/api/task/{tid}/progress',data={'progress':'10','note':'内部完整日志不应出现在邮件中','version':'1'})
        self.assertEqual(response.status_code,200,response.get_json())
        job=self.job('daily');sent=[]
        generation=uuid4().hex
        self.db.execute('INSERT INTO member_email(member_id,address,verified,generation,requested_at) VALUES(?,?,1,?,?)',
                        (colleague,'colleague-private@example.com',generation,self.at.timestamp()))
        queue(self.db,colleague,'daily',self.at.date().isoformat(),generation,self.at.timestamp(),uuid4().hex);self.db.commit()
        other=dict(self.db.execute('SELECT * FROM mail_jobs WHERE member_id=? ORDER BY id DESC LIMIT 1',(colleague,)).fetchone())|{'attempts':1}
        self.assertEqual(process_job(self.db,job,self.at,sent.append),'sent')
        self.assertEqual(process_job(self.db,other,self.at,sent.append),'sent')
        self.assertEqual([message['To'] for message in sent],['member@example.com','colleague-private@example.com'])
        for message in sent:
            self.assertIsNone(message['Cc']);self.assertIsNone(message['Bcc'])
            self.assertEqual([p.get_content_type() for p in message.iter_parts()],['text/plain','text/html'])
            for part in message.iter_parts():
                content=part.get_content()
                self.assertIn('收件隔离检查',content)
                self.assertNotIn('内部完整日志不应出现在邮件中',content)
                self.assertNotIn('colleague-private@example.com',content)
                self.assertNotIn('member@example.com',content)

    def test_disabled_groups_and_full_progress_do_not_nudge(self):
        pid=self.project(groups={'mechanical':[self.mid],'vision':[self.mid]})
        old=(self.at-timedelta(hours=49)).strftime('%Y-%m-%d %H:%M:%S')
        self.db.execute('UPDATE daily_groups SET enabled_at=?,progress=100 WHERE project_id=? AND stage=?',(old,pid,'mechanical'))
        self.db.execute('UPDATE daily_groups SET enabled_at=?,enabled=0 WHERE project_id=? AND stage=?',(old,pid,'vision'));self.db.commit()
        project=next(p for p in digest_items(self.db,self.mid,self.at)[1] if p['id']==pid)
        self.assertEqual(project['progress'],100)
        self.assertEqual(project['groups'],[{'label':'机械','progress':100,'nudge':False}])

    def test_beijing_window_deadlines_and_weekends(self):
        self.assertFalse(daily_window(self.at-timedelta(seconds=1)))
        self.assertTrue(daily_window(self.at.astimezone(timezone.utc)))
        self.assertTrue(daily_window(self.at.replace(hour=23,minute=59,second=59)))
        self.assertFalse(daily_window(self.at+timedelta(hours=2),self.at.date().isoformat()))
        weekend=datetime(2026,10,17,14,tzinfo=timezone.utc)
        self.bind();enqueue_daily(self.db,weekend)
        self.assertEqual(self.db.execute('SELECT day FROM mail_jobs WHERE member_id=?',(self.mid,)).fetchone()[0],'2026-10-17')
        self.assertEqual(deadline('2026-10-15',self.at.astimezone(timezone.utc)),'今天截止')
        self.assertEqual(deadline('2026-10-14',self.at),'已逾期 1 天')
        self.assertEqual(deadline('2026-10-18',self.at),'剩余 3 天')
        self.assertEqual(deadline(None,self.at),'未设置截止日期')
        self.assertFalse(stale((self.at-timedelta(hours=48)).astimezone(timezone.utc).isoformat(),10,self.at))

    def test_daily_retry_never_crosses_midnight_and_claimed_job_rechecks_window(self):
        self.task(title='午夜重试任务')
        job=self.job('daily');transport=MagicMock(side_effect=DeliveryError('临时失败',retry=True))
        late=self.at.replace(hour=23,minute=44)
        self.assertEqual(process_job(self.db,job,late,transport),'pending')
        self.assertEqual(self.db.execute('SELECT next_at FROM mail_jobs WHERE id=?',(job['id'],)).fetchone()[0],late.timestamp()+900)
        self.assertEqual(process_job(self.db,job,late+timedelta(minutes=1),transport),'failed')
        not_called=MagicMock()
        self.assertEqual(process_job(self.db,job,self.at+timedelta(hours=2),not_called),'skipped')
        self.assertEqual(process_job(self.db,job,self.at-timedelta(seconds=1),not_called),'skipped')
        not_called.assert_not_called()

    def test_test_mail_transient_retry_permanent_unknown_and_limit(self):
        job=self.job();transport=MagicMock(side_effect=DeliveryError('暂时失败',retry=True))
        self.assertEqual(process_job(self.db,job,self.at,transport),'pending')
        self.assertEqual(self.db.execute('SELECT next_at FROM mail_jobs WHERE id=?',(job['id'],)).fetchone()[0],self.at.timestamp()+60)
        self.assertEqual(process_job(self.db,job|{'attempts':3},self.at,transport),'failed')
        self.assertEqual(process_job(self.db,job,self.at,MagicMock(side_effect=DeliveryError('收件人拒绝'))),'failed')
        self.assertEqual(process_job(self.db,job,self.at,MagicMock(side_effect=DeliveryError('未知','unknown'))),'unknown')

    def test_verification_mail_retry_is_fast_and_bounded(self):
        response=self.member.post('/api/account/email/request',json={'email':'fast@example.com'})
        self.assertEqual(response.status_code,202,response.get_json())
        job=dict(self.db.execute("SELECT * FROM mail_jobs WHERE member_id=? AND kind='verify' ORDER BY id DESC LIMIT 1",(self.mid,)).fetchone())|{'attempts':1}
        now=datetime.now(TZ)
        transport=MagicMock(side_effect=DeliveryError('临时失败',retry=True))
        self.assertEqual(process_job(self.db,job,now,transport),'pending')
        state=self.db.execute("SELECT next_at,attempts FROM mail_jobs WHERE id=?",(job['id'],)).fetchone()
        self.assertEqual(state['next_at'],now.timestamp()+60)
        self.assertEqual(process_job(self.db,job|{'attempts':3},now,transport),'failed')

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
        self.assertEqual(client.post('/login',json={'username':'admin','password':'ci-test-password-only','next':'/daily-projects/1'}).get_json()['redirect'],'/tasks?type=daily&project=1')

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
        code=verification_code(binding['generation'])
        for part in sent[0].walk():
            if part.get_content_type() in ('text/plain','text/html'):
                body=part.get_content()
                self.assertIn(code,body)
                self.assertNotIn('/account/email/verify#token=',body)
                self.assertNotIn('href=',body)
        self.assertNotIn(code,sent[0]['Subject'])
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
