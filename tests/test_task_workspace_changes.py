import unittest
from uuid import uuid4

from test_tasks import app_module
from common import get_db


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
        self.assertEqual(response.status_code,403)
        page=self.clients['member'].get('/tasks').get_data(as_text=True)
        self.assertNotIn('id="new-task-toggle"',page)
        self.assertNotIn('href="/manage"',page)
        self.assertEqual(self.clients['member'].post('/api/task',json={'title':'forged'}).status_code,403)
        for client in (self.admin,self.clients['manager']):
            self.assertIn('id="create-form"',client.get('/tasks').get_data(as_text=True))
            self.assertEqual(client.get('/manage').status_code,200)
            self.assertNotIn('id="create-form"',client.get('/manage').get_data(as_text=True))

    def test_admin_can_be_new_owner_or_participant(self):
        admin_id = self.admin.get('/api/account/profile').get_json()['profile']['id']
        for body in ({'owner_id': admin_id},
                     {'owner_id': self.ids['member'], 'participant_ids': [admin_id]}):
            response = self.admin.post('/api/task', json={'title': 'assigned', **body})
            self.assertEqual(response.status_code, 200, response.get_json())
        task = self.admin.post('/api/task', json={
            'title': 'editable', 'owner_id': self.ids['member']}).get_json()
        response = self.admin.post(f"/api/task/{task['id']}", json={
            'version': task['version'], 'participant_ids': [admin_id]})
        self.assertEqual(response.status_code, 200, response.get_json())
        updated = next(item for item in self.admin.get('/api/data').get_json()['tasks']
                       if item['id'] == task['id'])
        self.assertIn(admin_id, updated['participant_ids'])

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

    def test_local_collaboration_is_available_without_task_management_permission(self):
        for client in (self.admin, self.clients['manager'], self.clients['member']):
            response = client.post('/discussions/api/topics', json={
                'submission_id': str(uuid4()), 'title': '协作独立权限', 'body': '所有登录成员可发起交流',
            })
            self.assertEqual(response.status_code, 201, response.get_json())
            detail = self.clients['member'].get(f"/discussions/api/topics/{response.get_json()['topic_id']}/messages")
            self.assertEqual(detail.status_code, 200, detail.get_json())
