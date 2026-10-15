"""Daily goals remain independent from vehicle tasks and authorization."""
import io
import unittest
from unittest.mock import patch
from uuid import uuid4
from PIL import Image
from test_tasks import app_module
from common import get_db
from modules.daily_schema import migrate
from modules.daily_projects import dashboard_selection


class DailyProjectsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.admin = cls.app.test_client()
        cls.admin.post('/login', json={'username':'admin','password':'ci-test-password-only'})
        cls.ids, cls.clients = [], []
        for role in ('member','member','manager'):
            name='daily_'+uuid4().hex[:10]
            result=cls.admin.post('/api/member',json={'name':name,'username':name,'password':'daily-password','role':role}).get_json()
            cls.ids.append(result['id'])
            client=cls.app.test_client(); client.post('/login',json={'username':name,'password':'daily-password'})
            cls.clients.append(client)

    def create(self, groups=None, **extra):
        body={'title':'共同目标','groups':groups or {'mechanical':[self.ids[0]],'vision':[self.ids[1]]}, **extra}
        response=self.admin.post('/api/daily-projects',json=body)
        self.assertEqual(response.status_code,200,response.get_json())
        return response.get_json()['id']

    def get(self,pid):
        return self.admin.get(f'/api/daily-projects/{pid}').get_json()['project']

    def update(self,pid,stage='mechanical',client=None,**data):
        return (client or self.clients[0]).post(f'/api/daily-projects/{pid}/groups/{stage}/progress',json={'progress':50,'version':1,'project_version':1,**data})

    def save(self,pid,**changes):
        old=self.get(pid)
        body={k:old[k] for k in ('title','detail','status','priority','start_date','due_date','version')}
        body['groups']={g['stage']:[o['id'] for o in g['owners']] for g in old['groups'] if g['enabled']}
        body.update(changes)
        return self.admin.post(f'/api/daily-projects/{pid}',json=body)

    def test_multiple_owners_average_and_no_vehicle_rows(self):
        before=len(self.admin.get('/api/data').get_json()['tasks'])
        pid=self.create({'mechanical':self.ids[:2],'electrical':[self.ids[0]],'vision':[self.ids[1]]})
        self.assertEqual(self.update(pid,progress=100).status_code,200)
        project=self.get(pid)
        self.assertEqual(project['progress'],33.3)
        self.assertEqual(project['owner_ids'],self.ids[:2])
        self.assertEqual(project['status'],'未开始')
        self.assertEqual(len(self.admin.get('/api/data').get_json()['tasks']),before)

    def test_group_authorization_and_manager(self):
        pid=self.create()
        self.assertEqual(self.update(pid,'vision').status_code,403)
        self.assertEqual(self.clients[0].post('/api/daily-projects',json={'title':'x','groups':{'vision':[self.ids[0]]}}).status_code,403)
        self.assertEqual(self.update(pid,'vision',self.clients[2]).status_code,200)

    def test_cross_group_success_same_group_conflict(self):
        pid=self.create()
        self.assertEqual(self.update(pid).status_code,200)
        self.assertEqual(self.update(pid,'vision',self.clients[1]).status_code,200)
        self.assertEqual(self.update(pid).status_code,409)
        self.assertEqual(self.get(pid)['version'],1)

    def test_assignment_change_invalidates_old_form(self):
        pid=self.create()
        self.assertEqual(self.save(pid,title='新版').status_code,200)
        self.assertEqual(self.update(pid).status_code,409)
        self.assertEqual(self.update(pid,project_version=2).status_code,200)
        self.save(pid,groups={'mechanical':[self.ids[1]]})
        self.assertEqual(self.update(pid,project_version=3,version=2).status_code,403)

    def test_disable_restore_preserves_progress_history(self):
        pid=self.create();self.update(pid,progress=80,note='机械记录')
        self.assertEqual(self.save(pid,groups={'vision':[self.ids[1]]}).status_code,200)
        self.assertEqual(self.get(pid)['progress'],0)
        self.assertEqual(self.update(pid,project_version=2,version=2).status_code,409)
        self.save(pid,groups={'mechanical':[self.ids[0]],'vision':[self.ids[1]]})
        self.assertEqual(self.get(pid)['progress'],40)
        self.assertEqual(self.admin.get(f'/api/daily-projects/{pid}/groups/mechanical/progress').get_json()['updates'][0]['note'],'机械记录')

    def test_closed_reopen_and_soft_delete(self):
        pid=self.create();self.save(pid,status='已完成')
        self.assertEqual(self.update(pid,project_version=2).status_code,409)
        self.assertEqual(self.save(pid,title='不能直接编辑').status_code,409)
        self.save(pid,status='进行中');self.assertEqual(self.update(pid,project_version=3).status_code,200)
        self.assertEqual(self.admin.post(f'/api/daily-projects/{pid}/delete',json={'version':3}).status_code,200)
        self.assertEqual(self.admin.get(f'/api/daily-projects/{pid}').status_code,404)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT count(*) FROM daily_updates WHERE project_id=?',(pid,)).fetchone()[0],1)

    def test_invalid_progress_owners_dates(self):
        pid=self.create()
        for value in (True,1.5,-1,101,'50'):
            self.assertEqual(self.update(pid,progress=value).status_code,400)
        for changes in ({'groups':{}},{'groups':{'vision':[]}},{'groups':{'other':[self.ids[0]]}},{'start_date':'2026-05-10','due_date':'2026-05-01'}):
            self.assertEqual(self.save(pid,**changes).status_code,400)

    def test_note_only_and_image_failure_atomicity(self):
        pid=self.create()
        self.assertEqual(self.update(pid,progress=0,note='仍在联调').status_code,200)
        image=io.BytesIO();Image.new('RGB',(3,3),'red').save(image,'PNG');image.seek(0)
        url=f'/api/daily-projects/{pid}/groups/mechanical/progress'
        with patch('modules.daily_projects.finalize_staged',side_effect=OSError('disk')):
            response=self.clients[0].post(url,data={'progress':'40','version':'2','project_version':'1','files':(image,'test.png')})
        self.assertEqual(response.status_code,500)
        self.assertEqual(self.get(pid)['groups'][0]['progress'],0)
        image.seek(0) if not image.closed else None
        self.assertEqual(len(self.admin.get(url).get_json()['updates']),1)

    def test_image_download_history_pagination(self):
        pid=self.create()
        image=io.BytesIO();Image.new('RGB',(3,3),'red').save(image,'PNG');image.seek(0)
        self.assertEqual(self.clients[0].post(f'/api/daily-projects/{pid}/groups/mechanical/progress',data={'progress':'1','version':'1','project_version':'1','files':(image,'test.png')}).status_code,200)
        for version in range(2,23):self.update(pid,version=version,progress=version,note=str(version))
        url=f'/api/daily-projects/{pid}/groups/mechanical/progress'
        data=self.admin.get(url).get_json();self.assertEqual(len(data['updates']),20)
        data=self.admin.get(url+'?before='+str(data['next_before'])).get_json()
        img=data['updates'][-1]['images'][0]['url']
        response=self.admin.get(img);self.assertEqual(response.status_code,200);response.close()
        self.assertEqual(self.app.test_client().get(img).status_code,401)

    def test_dashboard_slots_dedup_and_alert_boundaries(self):
        def p(i,mine=False,days=None,status='未开始'):
            return {'id':i,'owner_ids':[1] if mine else [],'days_left':days,'due_date':None,'status':status}
        items=[p(i,True) for i in range(1,6)]+[p(i,False,-1) for i in range(6,11)]
        selected,total=dashboard_selection(items,1)
        self.assertEqual(len(selected),6);self.assertEqual(total,10)
        self.assertEqual(sum(1 in x['owner_ids'] for x in selected),3)
        for day,expected in [(-1,1),(0,1),(3,1),(4,0),(7,0),(None,0)]:
            self.assertEqual(dashboard_selection([p(1,days=day)],1)[1],expected)
        self.assertEqual(dashboard_selection([p(1,True,-1),p(2,days=None,status='阻塞')],1)[1],2)
        self.assertEqual(dashboard_selection([p(1,True,-1,'已完成')],1)[1],0)
        self.assertEqual(dashboard_selection([],1),([],0))
        self.assertEqual(len(dashboard_selection([p(i,True) for i in range(8)],1)[0]),6)

    def test_repeat_migration_preserves_data(self):
        pid=self.create();self.update(pid,progress=43)
        with self.app.app_context():migrate(get_db());migrate(get_db())
        self.assertEqual(self.get(pid)['groups'][0]['progress'],43)
