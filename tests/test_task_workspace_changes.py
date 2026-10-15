import io
import unittest
from uuid import uuid4
from unittest.mock import patch
from urllib.error import HTTPError
from email.message import Message

from test_tasks import app_module
from common import get_db
from modules.discussions import _remote, GitHubError


class WorkspaceChanges(unittest.TestCase):
    def setUp(self):
        self.app=app_module.app
        self.admin=self.app.test_client()
        self.admin.post('/login',json={'username':'admin','password':'ci-test-password-only'})
        self.clients={}
        self.ids={}
        for role in ('member','manager'):
            name='workspace_'+uuid4().hex[:10]
            self.ids[role]=self.admin.post('/api/member',json={'name':name,'username':name,'password':'workspace-pass','role':role}).get_json()['id']
            client=self.app.test_client();client.post('/login',json={'username':name,'password':'workspace-pass'})
            self.clients[role]=client

    def test_management_access_and_create_location(self):
        response=self.clients['member'].get('/manage')
        self.assertEqual(response.status_code,302)
        page=self.clients['member'].get('/tasks').get_data(as_text=True)
        self.assertNotIn('id="new-task-toggle"',page)
        self.assertNotIn('href="/manage"',page)
        self.assertEqual(self.clients['member'].post('/api/task',json={'title':'forged'}).status_code,403)
        for client in (self.admin,self.clients['manager']):
            self.assertIn('id="create-form"',client.get('/tasks').get_data(as_text=True))
            self.assertEqual(client.get('/manage').status_code,200)
            self.assertNotIn('id="create-form"',client.get('/manage').get_data(as_text=True))

    def test_admin_cannot_be_new_owner_or_participant(self):
        for body in ({'owner_id':1},{'owner_id':self.ids['member'],'participant_ids':[1]}):
            response=self.admin.post('/api/task',json={'title':'invalid',**body})
            self.assertEqual(response.status_code,400)
        task=self.admin.post('/api/task',json={'title':'valid','owner_id':self.ids['member']}).get_json()
        response=self.admin.post(f"/api/task/{task['id']}",json={'version':task['version'],'participant_ids':[1]})
        self.assertEqual(response.status_code,400)

    def test_latest_note_ignores_blank_progress_events(self):
        task=self.admin.post('/api/task',json={'title':'notes','owner_id':self.ids['member']}).get_json()
        with self.app.app_context():
            db=get_db()
            for note,author in (('第一条','第一位备注者'),('最近备注','最近备注者'),('   ','仅更新进度者')):
                db.execute('INSERT INTO task_progress_updates(task_id,actor_id,actor_name,progress,note,created_at) VALUES(?,?,?,?,?,?)',(task['id'],1,author,20,note,'2026-10-15 12:00:00'))
            db.commit()
        rows=self.admin.get('/api/data').get_json()['tasks']
        returned=next(t for t in rows if t['id']==task['id'])
        self.assertEqual(returned['latest_note'],'最近备注')
        self.assertEqual(returned['latest_note_author'],'最近备注者')
        self.assertEqual(returned['latest_progress']['actor_name'],'仅更新进度者')

    def test_task_without_note_has_empty_note_author(self):
        task=self.admin.post('/api/task',json={'title':'no notes','owner_id':self.ids['member']}).get_json()
        rows=self.admin.get('/api/data').get_json()['tasks']
        returned=next(t for t in rows if t['id']==task['id'])
        self.assertEqual(returned['latest_note'],'')
        self.assertEqual(returned['latest_note_author'],'')

    def test_github_cache_rate_cooldown_and_pagination(self):
        with self.app.app_context():
            for key in ('github_read_cache','github_cooldowns'):self.app.extensions.pop(key,None)
            with patch('modules.discussions._remote_uncached',return_value=[]) as remote:
                _remote('/issues');_remote('/issues')
                self.assertEqual(remote.call_count,1)
            with patch('modules.discussions._remote_uncached',side_effect=GitHubError('限流',503,120)) as remote:
                for path in ('/first','/second'):
                    with self.assertRaises(GitHubError):_remote(path)
                self.assertEqual(remote.call_count,1)
            for key in ('github_read_cache','github_cooldowns'):self.app.extensions.pop(key,None)
        fixture=[{'pull_request':{},'number':i} for i in range(30)]
        with patch('modules.discussions._remote',return_value=fixture):
            data=self.admin.get('/discussions/api/items?kind=issues').get_json()
        self.assertTrue(data['has_next']);self.assertEqual(data['items'],[])

    def test_github_rate_limit_classified_from_headers(self):
        headers=Message();headers['X-RateLimit-Remaining']='0';headers['Retry-After']='90'
        error=HTTPError('https://api.github.com',403,'Forbidden',headers,io.BytesIO(b'{}'))
        with self.app.app_context(),patch('modules.discussions.urlopen',side_effect=error):
            from modules.discussions import _remote_uncached
            with self.assertRaises(GitHubError) as raised:_remote_uncached('/issues')
            self.assertEqual(raised.exception.retry_after,90)
            self.assertIn('额度',str(raised.exception))
