"""Exception aggregation and discussion association use real auth with isolated data."""
from datetime import datetime, timedelta, timezone
from html import escape
import unittest
from uuid import uuid4
from unittest.mock import patch

from test_tasks import app_module
from common import get_db
from modules.item_topics import migrate
from modules.task_alerts import alert_page, collect_alerts
from modules.mail_service import digest_content, digest_items


class AlertsAndTopicLinksTest(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app
        self.admin = self.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
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

    def test_assets_and_management_alert_entry_are_rendered(self):
        """Catch PR split regressions where new JS/CSS/routes are never mounted."""
        for url in ('/', '/tasks?type=daily', '/discussions'):
            with self.subTest(url=url):
                response = self.admin.get(url)
                self.assertEqual(response.status_code, 200)
                markup = response.get_data(as_text=True)
                self.assertIn('item-topics.js', markup)
                self.assertIn('item-topics.css', markup)
        self.assertIn('href="/task-alerts"', self.admin.get('/').get_data(as_text=True))

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

    def test_dynamic_daily_groups_mail_scope_blocker_details_and_completed_exclusion(self):
        colleague_name = self.name + '_colleague'
        colleague = self.admin.post('/api/member', json={'name': colleague_name, 'username': colleague_name,
            'password': 'repair-password', 'role': 'member'})
        self.assertEqual(colleague.status_code, 200, colleague.get_json())
        labels = [self.name + '采购组', self.name + '培训组']
        stages = []
        for label in labels:
            group = self.admin.post('/api/groups', json={'name': label})
            self.assertEqual(group.status_code, 200, group.get_json())
            stages.append('group-' + str(group.get_json()['id']))
        title = '动态目录邮件项目 ' + self.name
        reason, next_action, follow_up = '<动态样件待到>', '联系供应商 & 协调组内复核', '2026-10-19'
        created = self.admin.post('/api/daily-projects', json={'title': title, 'status': '阻塞',
            'groups_mode': 'replace', 'groups': {stages[0]: [self.uid], stages[1]: [colleague.get_json()['id']]},
            'blocker_reason': reason, 'blocker_next_action': next_action,
            'blocker_helper_id': self.uid, 'blocker_follow_up': follow_up})
        self.assertEqual(created.status_code, 200, created.get_json())
        pid = created.get_json()['id']
        with self.app.app_context():
            db = get_db()
            cfg = dict(self.app.config['MAIL_SETTINGS'], base_url='https://example.invalid')
            with patch.dict(self.app.config, MAIL_SETTINGS=cfg):
                for kind, hour, expected_labels in (('morning', 8, labels[:1]), ('daily', 22, labels)):
                    at = datetime(2026, 10, 18, hour, tzinfo=timezone(timedelta(hours=8)))
                    with self.subTest(kind=kind, completed=False):
                        projects = [item for item in digest_items(db, self.uid, at, kind)[1] if item['id'] == pid]
                        self.assertEqual(len(projects), 1, '多个动态组仍只汇总一个项目')
                        self.assertEqual([group['label'] for group in projects[0]['groups']], expected_labels)
                        text, markup = digest_content(db, self.uid, at, kind)
                        for body in (text, markup):
                            self.assertEqual(body.count(title), 1)
                            for label in expected_labels:
                                self.assertIn(label, body)
                            if kind == 'morning':
                                self.assertNotIn(labels[1], body)
                        for label, value in (('阻塞原因', reason), ('下一步行动', next_action),
                                             ('协助成员', self.name), ('下次跟进', follow_up)):
                            self.assertIn(label + '：' + value, text)
                            self.assertIn(label + '：' + escape(value), markup)
                        self.assertNotIn(reason, markup, 'HTML 阻塞原因必须转义')
        for index, stage in enumerate(stages):
            project = self.admin.get(f'/api/daily-projects/{pid}').get_json()['project']
            group = next(item for item in project['groups'] if item['stage'] == stage)
            client = self.owner if index == 0 else self.admin
            saved = client.post(f'/api/daily-projects/{pid}/groups/{stage}/progress', json={
                'progress': 100, 'note': '完成本组工作', 'version': group['version'], 'project_version': project['version']})
            self.assertEqual(saved.status_code, 200, saved.get_json())
            current = self.admin.get(f'/api/daily-projects/{pid}').get_json()['project']
            self.assertEqual((current['status'], current['progress']), ('阻塞', 50) if index == 0 else ('已完成', 100))
        with self.app.app_context():
            db = get_db()
            with patch.dict(self.app.config, MAIL_SETTINGS=cfg):
                for kind, hour in (('morning', 8), ('daily', 22)):
                    at = datetime(2026, 10, 18, hour, tzinfo=timezone(timedelta(hours=8)))
                    with self.subTest(kind=kind, completed=True):
                        self.assertNotIn(pid, [item['id'] for item in digest_items(db, self.uid, at, kind)[1]])
                        content = digest_content(db, self.uid, at, kind)
                        for body in content or ():
                            self.assertNotIn(title, body)


if __name__ == '__main__': unittest.main()
