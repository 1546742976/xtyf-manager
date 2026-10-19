"""Local collaboration contracts: real auth, revisions, files and concurrency."""
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch
from uuid import uuid4

from flask.testing import FlaskClient
from PIL import Image
# 先导入测试夹具（设置临时库/目录），再导入项目模块，避免绑定真实 data.db。
from test_tasks import app_module
from common import get_db

API = '/discussions/api'


class LocalCollaborationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.app
        cls.admin = cls.app.test_client()
        assert cls.admin.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only',
        }).get_json()['ok']
        cls.users = {}
        for role in ('author', 'reader', 'manager'):
            username = 'collaboration_' + uuid4().hex[:12]
            password = 'collaboration-test-password'
            response = cls.admin.post('/api/member', json={
                'name': '协作测试' + role, 'username': username, 'password': password,
                'role': 'manager' if role == 'manager' else 'member',
            })
            assert response.status_code == 200, response.get_json()
            client = cls.app.test_client()
            assert client.post('/login', json={'username': username, 'password': password}).get_json()['ok']
            cls.users[role] = (client, response.get_json()['id'], username, password)
        cls.author, cls.reader, cls.manager = (cls.users[name][0] for name in ('author', 'reader', 'manager'))
        cls.temp = tempfile.TemporaryDirectory(prefix='collaboration-files-')
        cls.previous_root = cls.app.config.get('TASK_ATTACHMENTS_DIR')
        cls.app.config['TASK_ATTACHMENTS_DIR'] = cls.temp.name

    @classmethod
    def tearDownClass(cls):
        if cls.previous_root is None:
            cls.app.config.pop('TASK_ATTACHMENTS_DIR', None)
        else:
            cls.app.config['TASK_ATTACHMENTS_DIR'] = cls.previous_root
        cls.temp.cleanup()

    def topic(self, client=None, **payload):
        client = client or self.author
        payload = {'submission_id': str(uuid4()), 'title': '测试-' + uuid4().hex,
                   'body': '首条消息', **payload}
        response = client.post(API + '/topics', json=payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def message(self, topic_id, client=None, **payload):
        client = client or self.author
        response = client.post(f'{API}/topics/{topic_id}/messages', json={
            'submission_id': str(uuid4()), 'body': '回复-' + uuid4().hex, **payload,
        })
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def rows(self, query, params=()):
        with self.app.app_context():
            return [dict(row) for row in get_db().execute(query, params)]

    def unread(self, client):
        response = client.get(API + '/unread')
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['total']

    def file_response(self, url):
        response = self.reader.get(url)
        self.addCleanup(response.close)
        return response

    def upload(self, topic_id, files, client=None, **payload):
        return (client or self.author).post(f'{API}/topics/{topic_id}/messages', data={
            'submission_id': str(uuid4()), 'body': '', **payload,
            'files': [(io.BytesIO(data), name, 'application/octet-stream') for name, data in files],
        }, content_type='multipart/form-data')

    def disk_files(self):
        return {str(path.relative_to(self.temp.name)): path.read_bytes()
                for path in Path(self.temp.name).rglob('*') if path.is_file()}

    def test_authentication_and_csrf_apply_to_every_write(self):
        topic = self.topic()
        tid, mid = topic['topic_id'], topic['message_id']
        anonymous = self.app.test_client()
        for path in ('/topics', f'/topics/{tid}/messages', f'/topics/{tid}/changes',
                     '/unread', '/submissions/' + str(uuid4())):
            self.assertEqual(anonymous.get(API + path).status_code, 401, path)
        writes = [('/topics', {'submission_id': str(uuid4()), 'title': '拒绝', 'body': '拒绝'}),
                  (f'/topics/{tid}/messages', {'submission_id': str(uuid4()), 'body': '拒绝'}),
                  (f'/messages/{mid}/withdraw', {}), (f'/topics/{tid}/withdraw', {}),
                  (f'/topics/{tid}/read', {'message_id': mid})]
        for path, payload in writes:
            with self.subTest(path=path):
                response = FlaskClient.open(self.author, API + path, method='POST', json=payload)
                self.assertEqual(response.status_code, 403, response.get_json())
                self.assertEqual(response.get_json()['reason'], 'csrf_failed')
        self.assertFalse(self.author.get(f'{API}/topics/{tid}/messages').get_json()['messages'][0]['withdrawn'])

    def test_title_body_limits_and_literal_text_are_preserved(self):
        title, body = '题' * 120, '文' * 5000
        topic = self.topic(title=title, body=body)
        self.assertEqual(topic['topic']['title'], title)
        self.assertEqual(topic['message']['body'], body)
        for fields in ({'title': '题' * 121}, {'title': ' \n '}, {'body': '文' * 5001}, {'body': 'x' + ' ' * 5000},
                       {'body': ' \n '}, {'submission_id': 'bad!'}):
            with self.subTest(fields=list(fields)):
                response = self.author.post(API + '/topics', json={
                    'submission_id': str(uuid4()), 'title': '有效标题', 'body': '有效正文', **fields,
                })
                self.assertEqual(response.status_code, 400, response.get_json())
        text = '  <script>alert("x")</script> **不是 HTML**\n第二行 & 中文\n  '
        reply = self.message(topic['topic_id'], body=text)
        self.assertEqual(reply['message']['body'], text)

    def test_wrong_json_shapes_and_numeric_cursor_types_are_rejected(self):
        for payload in ([], [1], '', 3, True, None):
            with self.subTest(payload=payload):
                response = self.author.post(API + '/topics', data=json.dumps(payload), content_type='application/json')
                self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        topic = self.topic()
        for path in ('/topics?page=-1', '/topics?page=1.5', '/topics?page=0',
                     f"/topics/{topic['topic_id']}/messages?before=-1",
                     f"/topics/{topic['topic_id']}/changes?after=abc"):
            self.assertEqual(self.reader.get(API + path).status_code, 400, path)
        for value in (True, 1.5, -1, '1.5'):
            response = self.reader.post(f"{API}/topics/{topic['topic_id']}/read", json={'message_id': value})
            self.assertEqual(response.status_code, 400, response.get_json())

    def test_topic_search_paginates_only_matching_rows(self):
        prefix = '分页-' + uuid4().hex
        expected = {self.topic(title=f'{prefix}-{index}')['topic_id'] for index in range(22)}
        first = self.reader.get(API + '/topics?page=1&q=' + quote(prefix)).get_json()
        second = self.reader.get(API + '/topics?page=2&q=' + quote(prefix)).get_json()
        self.assertEqual((first['page'], first['total'], len(first['topics']), first['has_more']), (1, 22, 20, True))
        self.assertEqual((second['page'], second['total'], len(second['topics']), second['has_more']), (2, 22, 2, False))
        self.assertEqual({row['id'] for row in first['topics'] + second['topics']}, expected)

    def test_message_withdrawal_119_allowed_120_denied_and_managers_override(self):
        for seconds, allowed in ((119, True), (120, False)):
            with self.subTest(seconds=seconds):
                with patch('modules.discussions.epoch_now', return_value=1_800_000_000):
                    topic = self.topic()
                endpoint = f"{API}/messages/{topic['message_id']}/withdraw"
                with patch('modules.discussions.epoch_now', return_value=1_800_000_000 + seconds):
                    self.assertEqual(self.reader.post(endpoint, json={}).status_code, 403)
                    result = self.author.post(endpoint, json={})
                self.assertEqual(result.status_code, 200 if allowed else 403, result.get_json())
                if not allowed:
                    for moderator in (self.manager, self.admin):
                        with patch('modules.discussions.epoch_now', return_value=1_800_000_000):
                            late = self.topic()
                        with patch('modules.discussions.epoch_now', return_value=1_800_001_000):
                            result = moderator.post(f"{API}/messages/{late['message_id']}/withdraw", json={})
                        self.assertEqual(result.status_code, 200, result.get_json())

    def test_topic_withdrawal_requires_no_reply_and_strict_author_window(self):
        for elapsed in (119, 120):
            with patch('modules.discussions.epoch_now', return_value=1_800_000_000):
                topic = self.topic()
            with patch('modules.discussions.epoch_now', return_value=1_800_000_000 + elapsed):
                result = self.author.post(f"{API}/topics/{topic['topic_id']}/withdraw", json={})
            self.assertEqual(result.status_code, 200 if elapsed == 119 else 403)
        topic = self.topic()
        reply = self.message(topic['topic_id'], self.reader)
        self.assertEqual(self.author.post(f"{API}/topics/{topic['topic_id']}/withdraw", json={}).status_code, 403)
        self.assertEqual(self.reader.post(f"{API}/messages/{reply['message_id']}/withdraw", json={}).status_code, 200)
        # A withdrawn reply is still a reply: it cannot revive author topic deletion.
        self.assertEqual(self.author.post(f"{API}/topics/{topic['topic_id']}/withdraw", json={}).status_code, 403)
        self.assertEqual(self.manager.post(f"{API}/topics/{topic['topic_id']}/withdraw", json={}).status_code, 200)

    def test_withdrawn_topic_is_a_tombstone_without_search_leaks(self):
        secret = '只属于被撤回话题-' + uuid4().hex
        topic = self.topic(title=secret, body='不可继续透露的正文')
        tid = topic['topic_id']
        result = self.admin.post(f'{API}/topics/{tid}/withdraw', json={})
        self.assertEqual(result.status_code, 200, result.get_json())
        listed = self.reader.get(API + '/topics').get_json()
        self.assertNotIn(secret, json.dumps(listed, ensure_ascii=False))
        search = self.reader.get(API + '/topics?q=' + quote(secret)).get_json()
        self.assertFalse(any(item['id'] == tid for item in search['topics']))
        detail = self.reader.get(f'{API}/topics/{tid}/messages')
        self.assertNotIn(secret, detail.get_data(as_text=True))
        self.assertNotIn('不可继续透露的正文', detail.get_data(as_text=True))
        response = self.author.post(f'{API}/topics/{tid}/messages', json={'submission_id': str(uuid4()), 'body': '不得回复'})
        self.assertIn(response.status_code, (403, 409, 410))

    def test_changes_include_withdrawals_but_never_removed_body(self):
        topic = self.topic()
        tid = topic['topic_id']
        cursor = self.reader.get(f'{API}/topics/{tid}/messages').get_json()['cursor']
        reply = self.message(tid, body='撤回后必须隐藏-' + uuid4().hex)
        body = reply['message']['body']
        self.assertEqual(self.author.post(f"{API}/messages/{reply['message_id']}/withdraw", json={}).status_code, 200)
        response = self.reader.get(f'{API}/topics/{tid}/changes?after={cursor}')
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertGreater(data['cursor'], cursor)
        self.assertTrue(any(event['kind'] == 'message_withdrawn' and event['message_id'] == reply['message_id'] for event in data['events']))
        self.assertNotIn(body, response.get_data(as_text=True))
        self.assertEqual(self.reader.get(f"{API}/topics/{tid}/changes?after={data['cursor']}").get_json()['events'], [])

    def test_message_cursor_pages_are_ascending_without_gaps(self):
        topic = self.topic()
        tid = topic['topic_id']
        expected = [topic['message_id']]
        for index in range(53):
            expected.append(self.message(tid, body=f'顺序 {index}')['message_id'])
        latest = self.reader.get(f'{API}/topics/{tid}/messages').get_json()
        self.assertEqual(len(latest['messages']), 50)
        self.assertTrue(latest['has_more'])
        older = self.reader.get(f"{API}/topics/{tid}/messages?before={latest['before']}").get_json()
        self.assertFalse(older['has_more'])
        ids = [row['id'] for row in older['messages'] + latest['messages']]
        self.assertEqual(ids, expected)
        events = self.reader.get(f'{API}/topics/{tid}/changes?after=0').get_json()
        self.assertEqual(len(events['events']), 50)
        self.assertTrue(events['has_more'])
        rest = self.reader.get(f"{API}/topics/{tid}/changes?after={events['cursor']}").get_json()
        self.assertFalse(rest['has_more'])
        event_ids = [event['id'] for event in events['events'] + rest['events']]
        self.assertEqual(event_ids, sorted(set(event_ids)))

    def test_own_messages_excluded_and_read_cursor_syncs_across_sessions(self):
        before_author, before_reader = self.unread(self.author), self.unread(self.reader)
        topic = self.topic()
        tid = topic['topic_id']
        self.assertEqual(self.unread(self.author), before_author)
        self.assertEqual(self.unread(self.reader), before_reader + 1)
        reply = self.message(tid, self.reader)
        self.assertEqual(self.unread(self.author), before_author + 1)
        _, _, username, password = self.users['author']
        second = self.app.test_client()
        self.assertTrue(second.post('/login', json={'username': username, 'password': password}).get_json()['ok'])
        second.get(f'{API}/topics/{tid}/messages')
        self.assertEqual(self.unread(self.author), before_author + 1, 'GET must not mark messages read')
        self.assertEqual(second.post(f'{API}/topics/{tid}/read', json={'message_id': reply['message_id']}).status_code, 200)
        self.assertEqual(self.unread(self.author), before_author)
        # A stale tab cannot move an existing cursor backwards.
        self.assertEqual(self.author.post(f'{API}/topics/{tid}/read', json={'message_id': topic['message_id']}).status_code, 200)
        self.assertEqual(self.unread(second), before_author)
        listed = second.get(API + '/topics?q=' + quote(topic['topic']['title'])).get_json()['topics']
        self.assertEqual(next(row for row in listed if row['id'] == tid)['read_message_id'], reply['message_id'])

    def test_withdrawal_removes_unread_and_wrong_topic_read_is_rejected(self):
        baseline = self.unread(self.reader)
        one, two = self.topic(), self.topic()
        self.assertEqual(self.unread(self.reader), baseline + 2)
        bad = self.reader.post(f"{API}/topics/{one['topic_id']}/read", json={'message_id': two['message_id']})
        self.assertEqual(bad.status_code, 404)
        self.assertEqual(self.unread(self.reader), baseline + 2)
        self.assertEqual(self.author.post(f"{API}/messages/{one['message_id']}/withdraw", json={}).status_code, 200)
        self.assertEqual(self.unread(self.reader), baseline + 1)

    def test_submission_lookup_recovers_lost_response_and_is_user_scoped(self):
        sid = str(uuid4())
        self.assertFalse(self.author.get(f'{API}/submissions/{sid}').get_json()['found'])
        body = {'submission_id': sid, 'title': '幂等-' + uuid4().hex, 'body': '只写入一次'}
        first = self.author.post(API + '/topics', json=body).get_json()
        again = self.author.post(API + '/topics', json=body)
        self.assertEqual(again.status_code, 200, again.get_json())
        self.assertEqual(again.get_json()['message_id'], first['message_id'])
        found = self.author.get(f'{API}/submissions/{sid}').get_json()
        self.assertEqual((found['found'], found['topic_id'], found['message_id']), (True, first['topic_id'], first['message_id']))
        self.assertFalse(self.reader.get(f'{API}/submissions/{sid}').get_json()['found'])
        messages = self.reader.get(f"{API}/topics/{first['topic_id']}/messages").get_json()['messages']
        self.assertEqual([row['id'] for row in messages], [first['message_id']])
        changed = self.author.post(API + '/topics', json={**body, 'body': '同 ID 不同内容'})
        self.assertEqual(changed.status_code, 409)

    def test_attachment_retry_checks_fingerprint_and_does_not_duplicate_files(self):
        tid, sid = self.topic()['topic_id'], str(uuid4())
        first = self.upload(tid, [('方案.cad', b'original-drawing')], submission_id=sid)
        self.assertEqual(first.status_code, 201, first.get_json())
        files = self.disk_files()
        again = self.upload(tid, [('方案.cad', b'original-drawing')], submission_id=sid)
        self.assertEqual(again.status_code, 200, again.get_json())
        self.assertEqual(again.get_json()['message_id'], first.get_json()['message_id'])
        self.assertEqual(self.disk_files(), files)
        for name, payload in [('方案.cad', b'changed'), ('另一方案.cad', b'original-drawing')]:
            changed = self.upload(tid, [(name, payload)], submission_id=sid)
            self.assertEqual(changed.status_code, 409, changed.get_json())
            self.assertEqual(self.disk_files(), files)

    def test_concurrent_retries_create_exactly_one_reply(self):
        topic = self.topic()
        sid, barrier = str(uuid4()), threading.Barrier(2)
        clients = [self.app.test_client(), self.app.test_client()]
        _, _, username, password = self.users['author']
        for client in clients:
            self.assertTrue(client.post('/login', json={'username': username, 'password': password}).get_json()['ok'])
        def submit(client):
            barrier.wait(timeout=10)
            result = client.post(f"{API}/topics/{topic['topic_id']}/messages", json={'submission_id': sid, 'body': '并发同一提交'})
            return result.status_code, result.get_json()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, clients))
        self.assertEqual(sorted(row[0] for row in results), [200, 201], results)
        self.assertEqual(results[0][1]['message_id'], results[1][1]['message_id'])
        messages = self.reader.get(f"{API}/topics/{topic['topic_id']}/messages").get_json()['messages']
        self.assertEqual(sum(row['body'] == '并发同一提交' for row in messages), 1)

    def test_arbitrary_original_types_roundtrip_with_safe_download_headers(self):
        tid = self.topic()['topic_id']
        for name in ('技术说明.pdf', '需求文档.docx', '装配.cad', '资料.zip', '程序.exe',
                     '页面.html', '绘图.svg', '没有扩展名'):
            with self.subTest(name=name):
                raw = b'original\x00\xff' + name.encode('utf-8') + b'<script>alert(1)</script>'
                result = self.upload(tid, [(name, raw)])
                self.assertEqual(result.status_code, 201, result.get_json())
                attachment = result.get_json()['message']['attachments'][0]
                self.assertEqual((attachment['name'], attachment['size']), (name, len(raw)))
                self.assertFalse(attachment.get('thumbnail_url'))
                response = self.file_response(attachment['download_url'])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, raw)
                self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')
                self.assertTrue(response.headers['Content-Disposition'].startswith('attachment;'))
                self.assertIn(quote(name), response.headers['Content-Disposition'])
                self.assertEqual(response.mimetype, 'application/octet-stream')
                anonymous = self.app.test_client()
                self.assertEqual(anonymous.get(attachment['download_url']).status_code, 401)

    def test_safe_thumbnail_does_not_replace_or_modify_original(self):
        tid = self.topic()['topic_id']
        image = io.BytesIO()
        Image.new('RGB', (37, 23), 'pink').save(image, format='PNG')
        raw = image.getvalue()
        response = self.upload(tid, [('原始照片.png', raw)])
        self.assertEqual(response.status_code, 201, response.get_json())
        attachment = response.get_json()['message']['attachments'][0]
        self.assertTrue(attachment['thumbnail_url'])
        thumb = self.file_response(attachment['thumbnail_url'])
        self.assertEqual(thumb.status_code, 200)
        self.assertEqual(thumb.mimetype, 'image/png')
        self.assertEqual(thumb.headers.get('X-Content-Type-Options'), 'nosniff')
        with Image.open(io.BytesIO(thumb.data)) as decoded:
            self.assertEqual(decoded.format, 'PNG')
        self.assertEqual(self.file_response(attachment['download_url']).data, raw)
        self.assertEqual(self.manager.post(f'{API}/topics/{tid}/withdraw', json={}).status_code, 200)
        self.assertEqual(self.reader.get(attachment['download_url']).status_code, 404)
        self.assertEqual(self.reader.get(attachment['thumbnail_url']).status_code, 404)

    def test_unsafe_or_oversized_image_preview_keeps_downloadable_original(self):
        tid = self.topic()['topic_id']
        image = io.BytesIO()
        Image.new('RGB', (20, 20), 'blue').save(image, format='PNG')
        raw = image.getvalue()
        for cap in ({'MAX_PIXELS': 399}, {'MAX_SIDE': 19}):
            with self.subTest(cap=cap), patch.multiple('modules.collaboration_storage', **cap):
                response = self.upload(tid, [('超出预览限制.png', raw)])
            self.assertEqual(response.status_code, 201, response.get_json())
            attachment = response.get_json()['message']['attachments'][0]
            self.assertFalse(attachment.get('thumbnail_url'))
            self.assertEqual(self.file_response(attachment['download_url']).data, raw)
        for name, raw in [('坏图片.png', b'not actually a PNG'),
                          ('矢量.svg', b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')]:
            response = self.upload(tid, [(name, raw)])
            self.assertEqual(response.status_code, 201, response.get_json())
            attachment = response.get_json()['message']['attachments'][0]
            self.assertFalse(attachment.get('thumbnail_url'))
            self.assertEqual(self.file_response(attachment['download_url']).data, raw)

    def test_original_filename_and_stored_path_cannot_escape_private_root(self):
        tid = self.topic()['topic_id']
        response = self.upload(tid, [('../../越界.html', b'private-content')])
        self.assertEqual(response.status_code, 201, response.get_json())
        attachment = response.get_json()['message']['attachments'][0]
        self.assertEqual(attachment['name'], '越界.html')
        row = self.rows('SELECT * FROM collaboration_attachments WHERE id=?', (attachment['id'],))[0]
        self.assertNotIn('越界', row['stored_name'])
        outside = Path(self.temp.name) / 'secret.txt'
        outside.write_bytes(b'never-expose')
        try:
            for unsafe in ('../secret.txt', '..\\secret.txt', str(outside.resolve())):
                with self.app.app_context():
                    db = get_db()
                    db.execute('UPDATE collaboration_attachments SET stored_name=? WHERE id=?', (unsafe, attachment['id']))
                    db.commit()
                result = self.reader.get(attachment['download_url'])
                self.assertEqual(result.status_code, 404, result.get_data(as_text=True))
                self.assertNotIn(b'never-expose', result.data)
        finally:
            with self.app.app_context():
                db = get_db()
                db.execute('UPDATE collaboration_attachments SET stored_name=? WHERE id=?', (row['stored_name'], attachment['id']))
                db.commit()
            outside.unlink()

    def test_withdrawn_files_are_retained_but_not_served_or_listed(self):
        tid = self.topic()['topic_id']
        response = self.upload(tid, [('撤回原件.zip', b'keep-for-snapshot')])
        self.assertEqual(response.status_code, 201, response.get_json())
        message = response.get_json()['message']
        attachment = message['attachments'][0]
        files_before = self.disk_files()
        self.assertEqual(self.author.post(f"{API}/messages/{message['id']}/withdraw", json={}).status_code, 200)
        self.assertEqual(self.reader.get(attachment['download_url']).status_code, 404)
        rows = self.reader.get(f'{API}/topics/{tid}/messages').get_json()['messages']
        withdrawn = next(row for row in rows if row['id'] == message['id'])
        self.assertEqual(withdrawn['attachments'], [])
        self.assertTrue(withdrawn['withdrawn'])
        self.assertEqual(self.disk_files(), files_before)

    def test_attachment_limits_and_failed_finalization_leave_no_partial_message(self):
        tid = self.topic()['topic_id']
        initial = self.reader.get(f'{API}/topics/{tid}/messages').get_json()['messages']
        initial_files = self.disk_files()
        cases = [({'MAX_FILE_BYTES': 8}, [('large.bin', b'x' * 9)]),
                 ({'MAX_FILE_BYTES': 8, 'MAX_TOTAL_BYTES': 10}, [('a.bin', b'a' * 6), ('b.bin', b'b' * 5)]),
                 ({}, [(f'{index}.bin', b'x') for index in range(6)])]
        for caps, files in cases:
            with self.subTest(caps=caps, count=len(files)):
                with patch.multiple('modules.collaboration_storage', **caps) if caps else nullcontext():
                    response = self.upload(tid, files)
                self.assertIn(response.status_code, (400, 413), response.get_json())
                self.assertEqual(self.disk_files(), initial_files)
                self.assertEqual(self.reader.get(f'{API}/topics/{tid}/messages').get_json()['messages'], initial)
        with patch('modules.discussions.finalize_staged', side_effect=OSError('synthetic disk full')):
            response = self.upload(tid, [('failure.bin', b'cannot-save')])
        self.assertGreaterEqual(response.status_code, 500)
        self.assertEqual(self.disk_files(), initial_files)
        self.assertEqual(self.reader.get(f'{API}/topics/{tid}/messages').get_json()['messages'], initial)

    def test_exact_attachment_caps_accept_valid_files(self):
        tid = self.topic()['topic_id']
        with patch.multiple('modules.collaboration_storage', MAX_FILE_BYTES=8, MAX_TOTAL_BYTES=10):
            single = self.upload(tid, [('exact.bin', b'12345678')])
            maximum_count = self.upload(tid, [(f'{index}.bin', b'12') for index in range(5)])
        self.assertEqual(single.status_code, 201, single.get_json())
        self.assertEqual(maximum_count.status_code, 201, maximum_count.get_json())
        self.assertEqual(sum(item['size'] for item in maximum_count.get_json()['message']['attachments']), 10)
        self.assertEqual(len(maximum_count.get_json()['message']['attachments']), 5)

    def test_partial_file_finalization_failure_rolls_back_all_database_records(self):
        from modules.collaboration_storage import finalize_staged
        tid = self.topic()['topic_id']
        before_files = self.disk_files()
        before_counts = {table: self.rows(f'SELECT count(*) AS n FROM {table}')[0]['n'] for table in (
            'collaboration_messages', 'collaboration_attachments', 'collaboration_submissions', 'collaboration_changes')}
        def partial_failure(staged):
            finalize_staged(staged[:1])
            raise OSError('disk failed after first rename')
        with patch('modules.discussions.finalize_staged', side_effect=partial_failure):
            response = self.upload(tid, [('a.cad', b'a'), ('b.cad', b'b')])
        self.assertGreaterEqual(response.status_code, 500)
        self.assertEqual(self.disk_files(), before_files)
        for table, count in before_counts.items():
            self.assertEqual(self.rows(f'SELECT count(*) AS n FROM {table}')[0]['n'], count, table)


class CollaborationMigrationTests(unittest.TestCase):
    def test_repeat_migration_preserves_existing_member_task_and_collaboration_rows(self):
        from modules.collaboration_schema import migrate
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE members(id INTEGER PRIMARY KEY,name TEXT)')
        db.execute("INSERT INTO members VALUES(1,'原成员')")
        db.execute('CREATE TABLE tasks(id INTEGER PRIMARY KEY,title TEXT)')
        db.execute("INSERT INTO tasks VALUES(1,'原任务')")
        db.commit()
        migrate(db)
        db.execute("INSERT INTO collaboration_topics VALUES(1,'保留话题',1,'原成员','date',1,'date',NULL,NULL)")
        db.execute("INSERT INTO collaboration_messages VALUES(1,1,1,'原成员','原消息','date',1,NULL,NULL)")
        db.execute("INSERT INTO collaboration_reads VALUES(1,1,1)")
        db.commit()
        for _ in range(2):
            migrate(db)
        self.assertEqual(db.execute('SELECT name FROM members').fetchone()[0], '原成员')
        self.assertEqual(db.execute('SELECT title FROM tasks').fetchone()[0], '原任务')
        self.assertEqual(db.execute('SELECT title FROM collaboration_topics').fetchone()[0], '保留话题')
        self.assertEqual(db.execute('SELECT body FROM collaboration_messages').fetchone()[0], '原消息')
        self.assertEqual(db.execute('SELECT message_id FROM collaboration_reads').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
