"""Exception aggregation and discussion association use real auth with isolated data."""
from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4
from unittest.mock import patch

from test_tasks import app_module
from common import get_db
from modules.item_topics import migrate
from modules.task_alerts import alert_page, collect_alerts
from modules.mail_service import digest_content


class AlertsAndTopicLinksTest(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app
        self.admin = self.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': '123456'})
        self.name = 'repair_' + uuid4().hex[:12]
        self.uid = self.admin.post('/api/member', json={'name': self.name, 'username': self.name,
            'password': 'repair-password', 'role': 'member'}).get_json()['id']
        self.owner = self.app.test_client()
        self.owner.post('/login', json={'username': self.name, 'password': 'repair-password'})

    def task(self, **extra):
        response = self.admin.post('/api/task', json={'title': self.name, 'owner_id': self.uid, **extra})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['id']

    def daily(self):
        response = self.admin.post('/api/daily-projects', json={'title': self.name, 'groups': {'mechanical': [self.uid]}})
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['id']

    def topic(self, body='结论内容'):
        response = self.admin.post('/discussions/api/topics', json={
            'submission_id': str(uuid4()), 'title': self.name, 'body': body})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_alerts_permission(self):
        self.assertEqual(self.owner.get('/api/task-alerts').status_code, 403)
        self.assertEqual(self.app.test_client().get('/api/task-alerts').status_code, 401)
        self.assertEqual(self.admin.get('/task-alerts').status_code, 200)
        self.assertEqual(self.admin.get('/api/task-alerts?page=0').status_code, 400)

    def test_midnight_48_hours_and_multi_label_deduplication(self):
        task = self.task(status='阻塞', due_date='2026-10-17', blocker_reason='等待样件', blocker_next_action='联系供应商')
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE tasks SET created_at='2026-10-15 23:59:59' WHERE id=?", (task,)); db.commit()
            at = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)
            result = alert_page(db, {'owner_id': str(self.uid)}, at)
            item = next(x for x in result['items'] if x['id'] == task and x['type'] == 'task')
            self.assertEqual(set(item['flags']), {'blocked', 'overdue', 'stale'})
            self.assertEqual(result['total'], 1)
            self.assertEqual(result['counts']['overdue'], 1)
            db.execute("UPDATE tasks SET created_at='2026-10-16 00:00:00',due_date='2026-10-18' WHERE id=?", (task,)); db.commit()
            item = alert_page(db, {'owner_id': str(self.uid)}, at)['items'][0]
            self.assertEqual(item['flags'], ['blocked'])

    def test_daily_flags_reenabled_and_inactive_owner(self):
        pid = self.daily()
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE daily_groups SET enabled_at='2026-10-18 00:00:00' WHERE project_id=?", (pid,))
            db.execute('UPDATE members SET is_active=0 WHERE id=?', (self.uid,)); db.commit()
            at = datetime(2026, 10, 18, 1, tzinfo=timezone(timedelta(hours=8)))
            item = next(x for x in collect_alerts(db, at) if x['type'] == 'daily' and x['id'] == pid)
            self.assertEqual(item['flags'], ['unassigned'])
            self.assertEqual(item['affected_groups'][0]['stage'], 'mechanical')

    def test_pagination_and_constant_query_count(self):
        for _ in range(22): self.task(due_date='2020-01-01')
        with self.app.app_context():
            db = get_db(); queries=[];db.set_trace_callback(queries.append)
            first = alert_page(db, {'owner_id': str(self.uid)})
            count = sum(q.lstrip().upper().startswith('SELECT') for q in queries)
            second = alert_page(db, {'owner_id': str(self.uid), 'page': '2'})
            db.set_trace_callback(None)
            self.assertEqual((first['total'], len(first['items']), len(second['items'])), (22, 20, 2))
            self.assertFalse({x['id'] for x in first['items']} & {x['id'] for x in second['items']})
            self.assertLessEqual(count, 8)

    def test_links_permissions_version_idempotence_and_no_progress(self):
        task = self.task(); topic = self.topic(); url = f'/api/task/{task}/topics'
        with self.app.app_context():
            before = dict(get_db().execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone())
        response = self.owner.post(url, json={'topic_id': topic['topic_id'], 'version': 1}).get_json()
        self.assertEqual(response['version'], 2)
        self.assertEqual(self.owner.post(url, json={'topic_id': topic['topic_id'], 'version': 1}).status_code, 409)
        again = self.owner.post(url, json={'topic_id': topic['topic_id'], 'version': 2}).get_json()
        self.assertEqual(again['version'], 2)
        with self.app.app_context():
            db = get_db(); after = dict(db.execute('SELECT * FROM tasks WHERE id=?', (task,)).fetchone())
            for key in ('progress','status','updated_at','completed_at'): self.assertEqual(before[key], after[key])
            self.assertEqual(db.execute('SELECT count(*) FROM task_progress_updates WHERE task_id=?',(task,)).fetchone()[0], 0)
            migrate(db); migrate(db)
            self.assertEqual(db.execute("SELECT count(*) FROM item_topic_events WHERE item_type='task' AND item_id=?",(task,)).fetchone()[0],1)

    def test_conclusion_ownership_withdrawal_and_backlinks(self):
        task = self.task(); first, other = self.topic('<私密结论>'), self.topic('其他话题')
        url = f'/api/task/{task}/topics'; tid = first['topic_id']
        self.owner.post(url, json={'topic_id': tid, 'version': 1})
        self.assertEqual(self.owner.post(url, json={'action':'conclusion','topic_id':tid,'conclusion_id':other['message_id'],'version':2}).status_code,400)
        response=self.owner.post(url,json={'action':'conclusion','topic_id':tid,'conclusion_id':first['message_id'],'version':2})
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertIn('<私密结论>', str(self.owner.get(url).get_json()))
        links=self.owner.get(f'/discussions/api/topics/{tid}/items').get_json()['items']
        self.assertEqual(links[0]['id'],task)
        withdrawn=self.admin.post(f'/discussions/api/messages/{first["message_id"]}/withdraw',json={})
        self.assertEqual(withdrawn.status_code,200,withdrawn.get_json())
        body=self.owner.get(url).get_json(); self.assertNotIn('<私密结论>',str(body))
        self.assertTrue(body['items'][0]['conclusion_withdrawn'])
        result=self.owner.post(url,json={'action':'unlink','topic_id':tid,'version':3})
        self.assertEqual(result.status_code,200,result.get_json())
        self.assertEqual(self.owner.get(f'/discussions/api/topics/{tid}/items').get_json()['items'],[])

    def test_daily_owner_link_permission_does_not_grant_management(self):
        pid=self.daily(); topic=self.topic(); url=f'/api/daily-projects/{pid}/topics'
        response=self.owner.post(url,json={'topic_id':topic['topic_id'],'version':1})
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(self.owner.post(f'/api/daily-projects/{pid}',json={'title':'越权','version':2}).status_code,403)
        with self.app.app_context():
            db=get_db(); db.execute('UPDATE daily_groups SET enabled=0 WHERE project_id=?',(pid,));db.commit()
        self.assertEqual(self.owner.post(url,json={'topic_id':topic['topic_id'],'version':2,'action':'unlink'}).status_code,403)

    def test_unrelated_member_cannot_link_task(self):
        task=self.task(owner_id=None);topic=self.topic()
        self.assertEqual(self.owner.post(f'/api/task/{task}/topics',json={'topic_id':topic['topic_id'],'version':1}).status_code,403)

    def test_blocker_mail_plain_html_consistency_and_filter_unchanged(self):
        task=self.task(status='阻塞',blocker_reason='<等待>',blocker_next_action='联系供应商',blocker_helper_id=self.uid,blocker_follow_up='2026-10-19')
        future=self.task(due_date='2099-12-01')
        with self.app.app_context():
            cfg=dict(self.app.config['MAIL_SETTINGS'],base_url='https://example.invalid')
            with patch.dict(self.app.config,MAIL_SETTINGS=cfg):
                text,html=digest_content(get_db(),self.uid,datetime(2026,10,18,8),'morning')
            self.assertIn('阻塞原因：<等待>',text);self.assertIn('&lt;等待&gt;',html)
            self.assertIn('联系供应商',text);self.assertIn('联系供应商',html)
            self.assertIn('2026-10-19',text);self.assertIn('2026-10-19',html)
            self.assertIn(f'/tasks?task={task}',text);self.assertNotIn(f'/tasks?task={future}',text)


if __name__ == '__main__': unittest.main()
