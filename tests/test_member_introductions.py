"""Public member introductions never expose account-management fields."""
import unittest
import io
import json
from uuid import uuid4
from test_tasks import app_module


class IntroductionTests(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        self.clients = []
        for _ in range(2):
            name = 'intro_' + uuid4().hex[:10]
            mid = self.admin.post('/api/member', json={'name': name, 'username': name, 'password': 'intro-password', 'role': 'member'}).get_json()['id']
            client = app_module.app.test_client()
            client.post('/login', json={'username': name, 'password': 'intro-password'})
            self.clients.append((client, mid))
        self.member, self.mid = self.clients[0]
        self.other, self.oid = self.clients[1]

    def test_self_edit_public_projection_and_normalization(self):
        initial = self.member.get('/api/account/introduction')
        self.assertEqual(initial.status_code, 200)
        data = initial.get_json()['profile']
        self.assertEqual(data['version'], 0)
        repo = {'url': 'http://github.com/example/project.git/', 'name': '代码', 'description': '简介'}
        response = self.member.post('/api/account/introduction', json={'version': 0, 'bio': '两行\n简介', 'repositories': [repo, repo]})
        self.assertEqual(response.status_code, 200, response.get_json())
        profile = response.get_json()['profile']
        self.assertEqual(profile['repositories'], [{'url': 'https://github.com/example/project', 'name': '代码', 'description': '简介'}])
        public = next(p for p in self.other.get('/api/collaboration/profiles?q='+profile['name']).get_json()['profiles'] if p['member_id'] == self.mid)
        self.assertEqual(public['repositories'], [])
        self.assertIsNone(public['github_url'])
        self.assertEqual(set(public), {'member_id', 'name', 'groups', 'avatar_url', 'bio', 'repositories', 'version', 'hidden', 'github_url', 'github_profiles', 'github_repositories'})
        self.assertEqual(self.other.post('/api/account/introduction', json={'version': 0, 'member_id': self.mid, 'bio': '覆盖', 'repositories': []}).status_code, 400)

    def test_multiple_links_public_preview_and_legacy_client_preserves_them(self):
        body = {'version': 0, 'bio': '专长',
                'github_profiles': [{'url': f'www.github.com/example{i}', 'description': f'主页 {i}'} for i in range(3)],
                'github_repositories': [{'url': f'github.com/example/project{i}', 'description': f'项目 {i}'} for i in range(3)]}
        saved = self.member.post('/api/account/introduction', json=body)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        profile = saved.get_json()['profile']
        self.assertEqual(profile['github_profiles'][0]['url'], 'https://github.com/example0')
        self.assertEqual(profile['github_repositories'][0]['description'], '项目 0')
        public = self.other.get('/api/collaboration/profiles?q='+profile['name']).get_json()['profiles'][0]
        self.assertEqual(public['github_profiles'], profile['github_profiles'])
        self.assertEqual(public['github_repositories'], profile['github_repositories'])
        old = self.member.post('/api/account/introduction', json={'version': 1, 'bio': '旧客户端', 'github_url': profile['github_url']}).get_json()['profile']
        self.assertEqual(old['github_profiles'], profile['github_profiles'])
        self.assertEqual(old['github_repositories'], profile['github_repositories'])
        body['version'] = old['version']
        body['bio'] = ''
        body['github_profiles'] = []
        saved = self.member.post('/api/account/introduction', json=body).get_json()['profile']
        self.assertIsNone(saved['github_url'])
        self.assertIn(self.mid, [p['member_id'] for p in self.other.get('/api/collaboration/profiles?q='+profile['name']).get_json()['profiles']])

    def test_multiple_link_limits_validation_and_deduplication(self):
        body = {'version': 0, 'bio': '', 'github_profiles': [], 'github_repositories': []}
        for field, url in [('github_profiles', 'github.com/example'), ('github_repositories', 'github.com/example/repo')]:
            for invalid in ([{'url': url}]*4, [{'url': 'javascript:alert(1)'}], [{'url': url, 'description': '字'*201}], {}, [{'url': url, 'member_id': self.oid}]):
                rejected = self.member.post('/api/account/introduction', json={**body, field: invalid})
                self.assertEqual(rejected.status_code, 400, rejected.get_json())
        body['github_profiles'] = [{'url': 'www.github.com/Example'}, {'url': 'https://github.com/example/'}]
        body['github_repositories'] = [{'url': 'www.github.com/example/repo.git'}, {'url': 'https://github.com/EXAMPLE/repo/'}]
        profile = self.member.post('/api/account/introduction', json=body).get_json()['profile']
        self.assertEqual(len(profile['github_profiles']), 1)
        self.assertEqual(len(profile['github_repositories']), 1)
        self.assertEqual(self.member.post('/api/account/introduction', json=body).status_code, 409)
        self.admin.post(f'/api/collaboration/profiles/{self.mid}/moderation', json={'version': 1, 'hidden': True, 'reason': '核实链接'})
        body['version'] = 2
        self.assertTrue(self.member.post('/api/account/introduction', json=body).get_json()['profile']['hidden'])
        self.assertNotIn(self.mid, [p['member_id'] for p in self.other.get('/api/collaboration/profiles').get_json()['profiles']])

    def test_personal_homepage_normalization_and_legacy_preservation(self):
        legacy = [{'url': 'https://github.com/team-a/project', 'name': '旧资料'}]
        saved = self.member.post('/api/account/introduction', json={'version': 0, 'bio': '', 'repositories': legacy})
        self.assertEqual(saved.status_code, 200)
        self.assertNotIn(self.mid, [p['member_id'] for p in self.other.get('/api/collaboration/profiles').get_json()['profiles']])
        for value in ('www.github.com/Example-User', 'github.com/Example-User/', 'http://www.github.com/Example-User', 'https://github.com/Example-User/'):
            prior = self.member.get('/api/account/introduction').get_json()['profile']
            result = self.member.post('/api/account/introduction', json={'version': prior['version'], 'bio': '', 'github_url': value})
            self.assertEqual(result.status_code, 200, result.get_json())
            own = result.get_json()['profile']
            self.assertEqual(own['github_url'], 'https://github.com/Example-User')
            self.assertEqual(own['repositories'][0]['url'], legacy[0]['url'])
        own = self.member.post('/api/account/introduction', json={'version': own['version'], 'bio': '旧客户端编辑', 'repositories': []}).get_json()['profile']
        self.assertEqual(own['github_url'], 'https://github.com/Example-User')
        public = next(p for p in self.other.get('/api/collaboration/profiles?q='+own['name']).get_json()['profiles'] if p['member_id'] == self.mid)
        self.assertEqual(public['repositories'], [])
        self.assertEqual(public['github_url'], own['github_url'])
        cleared = self.member.post('/api/account/introduction', json={'version': own['version'], 'bio': '', 'github_url': ''}).get_json()['profile']
        self.assertIsNone(cleared['github_url'])

    def test_homepage_rejects_non_profile_urls_and_bad_types(self):
        values = ['javascript:alert(1)', 'https://github.com/owner/repo', 'https://u:p@github.com/user',
                  'https://github.com:443/user', 'https://github.com/user?x=1', 'https://github.com/user#x',
                  'https://github.com/settings', 'https://github.com/login', 'https://github.com/explore',
                  'https://github.com/user%2frepo', 'https://evil.example/user', '//github.com/user',
                  'github.com/a b', 'www.github.com/-user', 'github.com/a--b', 3, [], {}, True]
        for value in values:
            response = self.member.post('/api/account/introduction', json={'version': 0, 'bio': '', 'github_url': value})
            self.assertEqual(response.status_code, 400, value)
        self.assertEqual(self.member.get('/api/account/introduction').get_json()['profile']['version'], 0)

    def test_homepage_keeps_hidden_state_and_conflict_draft(self):
        self.member.post('/api/account/introduction', json={'version': 0, 'bio': '', 'github_url': 'github.com/example'})
        path = f'/api/collaboration/profiles/{self.mid}/moderation'
        self.admin.post(path, json={'version': 1, 'hidden': True, 'reason': '核实'})
        draft = {'version': 1, 'bio': '保留草稿', 'github_url': 'www.github.com/new-user'}
        self.assertEqual(self.member.post('/api/account/introduction', json=draft).status_code, 409)
        draft['version'] = 2
        profile = self.member.post('/api/account/introduction', json=draft).get_json()['profile']
        self.assertTrue(profile['hidden'])
        self.assertEqual(profile['github_url'], 'https://github.com/new-user')
        self.assertEqual(profile['moderation_reason'], '核实')

    def test_homepage_migration_from_old_table_keeps_repository_data(self):
        import sqlite3
        from contextlib import closing
        from modules.member_introductions import migrate
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute("CREATE TABLE member_introductions(member_id INTEGER PRIMARY KEY,bio TEXT,repositories TEXT,version INTEGER)")
            db.execute("INSERT INTO member_introductions VALUES(1,'旧简介','[]',8)")
            db.commit()
            migrate(db); migrate(db)
            self.assertEqual(db.execute('SELECT bio,repositories,version,github_url FROM member_introductions').fetchone(), ('旧简介', '[]', 8, None))
            self.assertEqual(db.execute('SELECT github_profiles,github_repositories FROM member_introductions').fetchone(), (None, None))

    def test_hide_conflict_and_owner_edits_do_not_unhide(self):
        self.member.post('/api/account/introduction', json={'version': 0, 'bio': '公开', 'repositories': []})
        path = '/api/collaboration/profiles/' + str(self.mid) + '/moderation'
        self.assertEqual(self.other.post(path, json={'version': 1, 'hidden': True, 'reason': '无权'}).status_code, 403)
        self.assertEqual(self.admin.post(path, json={'version': 1, 'hidden': True, 'reason': '核实内容'}).status_code, 200)
        body = {'version': 1, 'bio': '草稿', 'repositories': []}
        self.assertEqual(self.member.post('/api/account/introduction', json=body).status_code, 409)
        body['version'] = 2
        body['bio'] = ''
        self.assertEqual(self.member.post('/api/account/introduction', json=body).status_code, 200)
        own = self.member.get('/api/account/introduction').get_json()['profile']
        self.assertTrue(own['hidden'])
        self.assertEqual(own['moderation_reason'], '核实内容')
        hidden = self.admin.get('/api/collaboration/profiles?include_hidden=1&q='+own['name']).get_json()['profiles']
        self.assertIn(self.mid, [p['member_id'] for p in hidden])
        self.assertNotIn(self.mid, [p['member_id'] for p in self.other.get('/api/collaboration/profiles').get_json()['profiles']])
        self.assertEqual(self.admin.post(path, json={'version': 3, 'hidden': False, 'reason': '已核实'}).status_code, 200)

    def test_reject_invalid_links_limits_and_anonymous(self):
        self.assertEqual(app_module.app.test_client().get('/api/collaboration/profiles').status_code, 401)
        for url in ('javascript:alert(1)', 'https://github.com/user', 'https://u:p@github.com/u/r', 'https://github.com/u/r/issues', 'https://evil.example/u/r', 'https://github.com/u/r?token=secret'):
            response = self.member.post('/api/account/introduction', json={'version': 0, 'bio': '', 'repositories': [{'url': url}]})
            self.assertEqual(response.status_code, 400, url)
        self.assertEqual(self.member.post('/api/account/introduction', json={'version': 0, 'bio': '字'*1001, 'repositories': []}).status_code, 400)

    def test_repeat_migration_keeps_content_and_search_paginates(self):
        from common import get_db, now_str
        from modules.member_introductions import migrate
        prefix = 'page_' + uuid4().hex[:8]
        with app_module.app.app_context():
            db = get_db()
            for i in range(14):
                mid = db.execute('INSERT INTO members(name,username,password_hash,role,group_name,is_active,created_at) VALUES(?,?,?,?,?,?,?)',
                    (prefix+str(i), prefix+str(i), 'not-a-real-password', 'member', prefix+'组', 1, now_str())).lastrowid
                db.execute('INSERT INTO member_introductions(member_id,bio,version,updated_at) VALUES(?,?,?,?)', (mid, '合成资料', 1, now_str()))
            db.commit()
            migrate(db); migrate(db)
        first = self.member.get('/api/collaboration/profiles?q='+prefix).get_json()
        second = self.member.get('/api/collaboration/profiles?q='+prefix+'&page=2').get_json()
        self.assertEqual((first['total'], len(first['profiles']), first['has_more']), (14, 12, True))
        self.assertEqual(len(second['profiles']), 2)
        self.assertFalse({p['member_id'] for p in first['profiles']} & {p['member_id'] for p in second['profiles']})
        self.assertEqual(self.member.get('/api/collaboration/profiles?page='+'9'*100).status_code, 400)

    def test_empty_inactive_and_csrf_do_not_bypass_visibility(self):
        result = self.member.post('/api/account/introduction', json={'version': 0, 'bio': '', 'repositories': []})
        self.assertEqual(result.status_code, 200)
        self.assertNotIn(self.mid, [p['member_id'] for p in self.other.get('/api/collaboration/profiles').get_json()['profiles']])
        result = self.member.post('/api/account/introduction', json={'version': 1, 'bio': '资料', 'repositories': []}, headers={'X-CSRF-Token': 'invalid'})
        self.assertEqual(result.status_code, 403)

    def test_public_avatar_is_authenticated_and_hidden_with_profile(self):
        from PIL import Image
        image = io.BytesIO()
        Image.new('RGB', (16, 16), 'green').save(image, format='PNG')
        image.seek(0)
        uploaded = self.member.post('/api/account/avatar', data={'avatar': (image, 'synthetic.png'), 'crop': json.dumps({'x': 0, 'y': 0, 'size': 16})})
        self.assertEqual(uploaded.status_code, 200, uploaded.get_json())
        self.member.post('/api/account/introduction', json={'version': 0, 'bio': '公开头像', 'repositories': []})
        path = f'/api/collaboration/profiles/{self.mid}/avatar'
        response = self.other.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/webp')
        self.assertEqual(app_module.app.test_client().get(path).status_code, 401)
        self.admin.post(f'/api/collaboration/profiles/{self.mid}/moderation', json={'version': 1, 'hidden': True, 'reason': '核实'})
        self.assertEqual(self.other.get(path).status_code, 404)
        self.assertEqual(self.member.get(path).status_code, 200)


if __name__ == '__main__':
    unittest.main()
