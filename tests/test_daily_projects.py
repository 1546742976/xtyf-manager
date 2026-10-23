"""Daily goals remain independent from vehicle tasks and authorization."""
import io
import sqlite3
from contextlib import closing
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
        project=self.get(pid)
        group=next(group for group in project['groups'] if group['stage']==stage)
        return (client or self.clients[0]).post(f'/api/daily-projects/{pid}/groups/{stage}/progress',json={'progress':50,'version':group['version'],'project_version':project['version'],**data})

    def save(self,pid,**changes):
        old=self.get(pid)
        body={k:old[k] for k in ('title','detail','status','priority','start_date','due_date','version')}
        body['groups']={g['stage']:[o['id'] for o in g['owners']] for g in old['groups'] if g['enabled']}
        body['group_versions']={g['stage']:g['version'] for g in old['groups']}
        body.update(changes)
        return self.admin.post(f'/api/daily-projects/{pid}',json=body)

    def test_image_decoding_does_not_hold_database_write_lock(self):
        pid = self.create()
        project = self.get(pid)
        group = next(g for g in project['groups'] if g['stage'] == 'mechanical')
        from modules import daily_projects
        original = daily_projects.prepare_image
        checked = []
        def decode(upload):
            with closing(sqlite3.connect(app_module.DB_PATH, timeout=0.1)) as other:
                other.execute('BEGIN IMMEDIATE')
                other.rollback()
                checked.append(True)
            return original(upload)
        image = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(image, format='PNG')
        image.seek(0)
        with patch.object(daily_projects, 'prepare_image', side_effect=decode):
            response = self.clients[0].post(f'/api/daily-projects/{pid}/groups/mechanical/progress', data={
                'progress':'50', 'version':str(group['version']), 'project_version':str(project['version']),
                'files':(image, 'small.png'),
            })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(checked, [True])

    def test_multiple_owners_average_and_no_vehicle_rows(self):
        before=len(self.admin.get('/api/data').get_json()['tasks'])
        pid=self.create({'mechanical':self.ids[:2],'electrical':[self.ids[0]],'vision':[self.ids[1]]})
        self.assertEqual(self.update(pid,progress=100).status_code,200)
        project=self.get(pid)
        self.assertEqual(project['progress'],33.3)
        self.assertEqual(project['owner_ids'],self.ids[:2])
        self.assertEqual(project['status'],'进行中')
        self.assertEqual(len(self.admin.get('/api/data').get_json()['tasks']),before)

    def test_group_authorization_and_manager(self):
        pid=self.create()
        self.assertEqual(self.update(pid,'vision').status_code,403)
        self.assertEqual(self.clients[0].post('/api/daily-projects',json={'title':'x','groups':{'vision':[self.ids[0]]}}).status_code,200)
        self.assertEqual(self.clients[0].post(f'/api/daily-projects/{pid}',json={}).status_code,403)
        self.assertEqual(self.update(pid,'vision',self.clients[2]).status_code,200)

    def test_cross_group_success_same_group_conflict(self):
        pid=self.create(status='进行中')
        self.assertEqual(self.update(pid,project_version=1).status_code,200)
        self.assertEqual(self.update(pid,'vision',self.clients[1],project_version=1).status_code,200)
        self.assertEqual(self.update(pid,version=1,project_version=1).status_code,409)
        self.assertEqual(self.get(pid)['version'],1)

    def test_assignment_change_invalidates_old_form(self):
        pid=self.create()
        self.assertEqual(self.save(pid,title='新版').status_code,200)
        self.assertEqual(self.update(pid,project_version=1).status_code,409)
        self.assertEqual(self.update(pid,project_version=2).status_code,200)
        self.save(pid,groups={'mechanical':[self.ids[1]]})
        self.assertEqual(self.update(pid,version=2).status_code,403)

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
        self.assertEqual(self.save(pid,status='进行中').status_code,400)
        self.assertEqual(self.save(pid,status='进行中',reopen_progress={'mechanical':25}).status_code,200)
        self.assertEqual(self.update(pid).status_code,200)
        self.assertEqual(self.admin.post(f'/api/daily-projects/{pid}/delete',json={'version':self.get(pid)['version']}).status_code,200)
        self.assertEqual(self.admin.get(f'/api/daily-projects/{pid}').status_code,404)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT count(*) FROM daily_updates WHERE project_id=?',(pid,)).fetchone()[0],4)

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

    def catalog_group(self, name='专业'):
        response=self.admin.post('/api/groups',json={'name':name+uuid4().hex[:8]})
        self.assertEqual(response.status_code,200,response.get_json())
        return response.get_json()['id']

    def test_dynamic_groups_legacy_edits_and_explicit_mapping(self):
        gid=self.catalog_group('硬件')
        dynamic=f'group-{gid}'
        pid=self.create({'mechanical':[self.ids[0]],dynamic:[self.ids[1]]},groups_mode='replace')
        self.assertEqual(self.update(pid,dynamic,self.clients[1],progress=70,note='硬件记录').status_code,200)
        self.assertEqual(self.save(pid,groups={'mechanical':[self.ids[0]]}).status_code,200)
        self.assertTrue(next(g for g in self.get(pid)['groups'] if g['stage']==dynamic)['enabled'])
        self.assertEqual(self.save(pid,groups={'mechanical':[self.ids[0]]},groups_mode='replace').status_code,200)
        self.assertFalse(next(g for g in self.get(pid)['groups'] if g['stage']==dynamic)['enabled'])
        self.assertEqual(self.admin.get(f'/api/daily-projects/{pid}/groups/{dynamic}/progress').get_json()['updates'][0]['note'],'硬件记录')
        legacy=self.get(pid)['groups'][0]
        self.assertIsNone(legacy['group_id'])
        mapped=self.catalog_group('改名机械')
        self.assertEqual(self.save(pid,group_links={'mechanical':mapped}).status_code,200)
        self.assertEqual(self.get(pid)['groups'][0]['stage'],'mechanical')
        self.assertEqual(self.get(pid)['groups'][0]['group_id'],mapped)
        self.assertEqual(self.save(pid,groups={'mechanical':[self.ids[0]],f'group-{mapped}':[self.ids[0]]},groups_mode='replace').status_code,400)

    def test_catalog_rename_archive_keeps_existing_project_and_history(self):
        gid=self.catalog_group();stage=f'group-{gid}'
        pid=self.create({stage:[self.ids[0]]},groups_mode='replace')
        renamed='重命名'+uuid4().hex[:8]
        self.assertEqual(self.admin.post(f'/api/groups/{gid}',json={'name':renamed}).status_code,200)
        self.assertEqual(self.get(pid)['groups'][0]['label'],renamed)
        self.assertEqual(self.admin.post(f'/api/groups/{gid}/delete',json={'confirm_name':renamed}).status_code,200)
        self.assertEqual(self.update(pid,stage,progress=30).status_code,200)
        self.assertEqual(self.save(pid,title='目录停用后仍可维护现有项目').status_code,200)
        self.assertEqual(self.get(pid)['groups'][0]['catalog_archived'],1)
        self.assertEqual(self.admin.post('/api/daily-projects',json={'title':'不许新增停用组','groups':{stage:[self.ids[0]]},'groups_mode':'replace'}).status_code,400)
        other=self.catalog_group();new_stage=f'group-{other}'
        self.assertEqual(self.save(pid,groups={new_stage:[self.ids[0]]},groups_mode='replace').status_code,200)
        self.assertEqual(self.save(pid,groups={stage:[self.ids[0]]},groups_mode='replace').status_code,400)

    def test_automatic_start_completion_and_reopening_invariant(self):
        pid=self.create()
        self.assertEqual(self.update(pid,progress=0,note='仅记录').status_code,200)
        self.assertEqual(self.get(pid)['status'],'未开始')
        first=self.update(pid,progress=1).get_json()
        self.assertEqual(first['project']['status'],'进行中')
        self.assertEqual(first['project_version'],2)
        self.update(pid,progress=100)
        self.assertEqual(self.get(pid)['status'],'进行中')
        self.update(pid,'vision',self.clients[1],progress=100)
        closed=self.get(pid)
        self.assertEqual((closed['status'],closed['progress']),('已完成',100))
        self.assertIsNotNone(closed['completed_at'])
        self.assertEqual(self.update(pid,progress=99).status_code,409)
        self.assertEqual(self.save(pid,status='进行中',reopen_progress={'mechanical':100}).status_code,400)
        self.assertEqual(self.save(pid,status='进行中',reopen_progress={'mechanical':40}).status_code,200)
        reopened=self.get(pid)
        self.assertEqual(reopened['status'],'进行中');self.assertIsNone(reopened['completed_at'])
        self.assertEqual({g['stage']:g['progress'] for g in reopened['groups']},{'mechanical':40,'vision':100})
        self.assertEqual(self.save(pid,status='已完成').status_code,200)
        self.assertTrue(all(g['progress']==100 for g in self.get(pid)['groups']))

    def test_average_never_rounds_incomplete_to_one_hundred(self):
        from modules.daily_projects import _finish_description
        groups=[{'enabled':1,'progress':100,'owners':[]} for _ in range(20)]
        groups[-1]['progress']=99
        self.assertEqual(_finish_description({'due_date':None},groups)['progress'],99.9)
        groups[-1]['progress']=100
        self.assertEqual(_finish_description({'due_date':None},groups)['progress'],100)

    def test_blocker_owner_authorization_resolution_and_terminal_events(self):
        pid=self.create({'mechanical':[self.ids[0]]})
        url=f'/api/daily-projects/{pid}/blocker'
        fields={'blocker_reason':'等待配件','blocker_next_action':'联系采购','blocker_helper_id':self.ids[1],'blocker_follow_up':'2026-10-20'}
        request={'version':1,'blocker_action':'open',**fields}
        self.assertEqual(self.clients[1].post(url,json=request).status_code,403)
        self.assertEqual(self.clients[0].post(url,json=request).status_code,200)
        self.assertEqual(self.clients[0].post(url,json=request).status_code,409)
        self.assertEqual(self.update(pid,progress=70).status_code,200)
        self.assertEqual(self.get(pid)['status'],'阻塞')
        version=self.get(pid)['version']
        self.assertEqual(self.clients[0].post(url,json={'version':version,'blocker_action':'resolve'}).status_code,400)
        self.assertEqual(self.clients[0].post(url,json={'version':version,'blocker_action':'resolve','blocker_result':'配件已到'}).status_code,200)
        resolved=self.get(pid)
        self.assertEqual((resolved['status'],resolved['progress']),('进行中',70))
        self.assertEqual(resolved['blocker_events'][0]['resolution'],'配件已到')
        self.assertEqual(self.clients[0].post(url,json={'version':resolved['version'],'blocked':True,**fields}).status_code,200)
        self.assertEqual(self.update(pid,progress=100).status_code,200)
        self.assertEqual(self.get(pid)['status'],'已完成')
        self.assertEqual(self.get(pid)['blocker_events'][0]['action'],'resolve')
        self.assertEqual(self.get(pid)['blocker_reason'],'')

    def test_manual_cancel_closes_blocker_and_stale_group_versions_reject(self):
        pid=self.create(status='阻塞',blocker_reason='等待方案',blocker_next_action='讨论')
        old=self.get(pid)
        self.update(pid,progress=10)
        self.assertEqual(self.save(pid,group_versions={g['stage']:g['version'] for g in old['groups']}).status_code,409)
        self.assertEqual(self.save(pid,status='已取消').status_code,200)
        self.assertIn('取消',self.get(pid)['blocker_events'][0]['resolution'])
        self.assertIsNone(self.get(pid)['completed_at'])

    def test_completion_by_group_selection_and_missing_versions(self):
        pid=self.create(status='进行中')
        self.update(pid,progress=100)
        self.assertEqual(self.save(pid,status='已完成',group_versions=None).status_code,400)
        self.assertEqual(self.save(pid,groups={'mechanical':[self.ids[0]]},groups_mode='replace',group_versions=None).status_code,400)
        self.assertEqual(self.save(pid,groups={'mechanical':[self.ids[0]]},groups_mode='replace').status_code,200)
        project=self.get(pid)
        self.assertEqual((project['status'],project['progress']),('已完成',100))
        self.assertEqual(next(g for g in project['groups'] if g['stage']=='vision')['progress'],0)

    def test_legacy_schema_upgrade_keeps_identifiers_and_no_guessed_mapping(self):
        import sqlite3
        from modules.daily_schema import SCHEMA
        db=sqlite3.connect(':memory:')
        try:
            db.executescript('CREATE TABLE members(id INTEGER PRIMARY KEY);CREATE TABLE groups(id INTEGER PRIMARY KEY,name TEXT,deleted_at TEXT);')
            db.executescript(SCHEMA)
            db.execute('INSERT INTO members VALUES(7)')
            db.execute("INSERT INTO groups VALUES(9,'机械',NULL)")
            db.execute("INSERT INTO daily_projects(id,title,created_at,updated_at) VALUES(17,'旧项目','2026-01-01','2026-01-01')")
            db.execute("INSERT INTO daily_groups(project_id,stage,enabled_at,progress) VALUES(17,'mechanical','2026-01-01',40)")
            db.execute("INSERT INTO daily_owners VALUES(17,'mechanical',7)")
            db.execute("INSERT INTO daily_updates VALUES(23,17,'mechanical',7,'原负责人',40,'历史','2026-01-01')")
            db.execute("INSERT INTO daily_attachments VALUES(31,23,'旧图片.png','stored.png','image/png')")
            db.commit();migrate(db);migrate(db)
            self.assertEqual(db.execute('SELECT project_id,stage,group_id,label_snapshot,progress FROM daily_groups').fetchone(),(17,'mechanical',None,'机械',40))
            self.assertEqual(db.execute('SELECT * FROM daily_owners').fetchone(),(17,'mechanical',7))
            self.assertEqual(db.execute('SELECT id,stage,note FROM daily_updates').fetchone(),(23,'mechanical','历史'))
            self.assertEqual(db.execute('SELECT id,update_id,stored_name FROM daily_attachments').fetchone(),(31,23,'stored.png'))
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        finally:
            db.close()

    def test_unrelated_edit_does_not_reconcile_historical_conflicts(self):
        for status,progress in [('未开始',40),('进行中',100),('阻塞',100)]:
            pid=self.create({'mechanical':[self.ids[0]]})
            with self.app.app_context():
                db=get_db();db.execute('UPDATE daily_groups SET progress=? WHERE project_id=?',(progress,pid))
                db.execute('UPDATE daily_projects SET status=? WHERE id=?',(status,pid));db.commit()
            fields={'title':'只改标题','blocker_reason':'','blocker_next_action':'','blocker_helper_id':None,'blocker_follow_up':''}
            self.assertEqual(self.save(pid,**fields).status_code,200)
            self.assertEqual(self.get(pid)['status'],status)
            self.assertIsNone(self.get(pid)['completed_at'])
            self.assertEqual(self.update(pid,progress=progress,note='补充备注，不改进度').status_code,200)
            self.assertEqual(self.get(pid)['status'],status)
