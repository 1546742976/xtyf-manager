"""Regressions for the defects reproduced during the whole-project review."""
import io
import csv
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import contextlib
import hashlib
from uuid import uuid4
from unittest.mock import patch

from test_tasks import app_module
from modules import invoices, funds
from modules.funds_revisions import migrate as migrate_revisions


class TestReviewFixes(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        name = 'review_' + uuid4().hex[:12]
        created = self.admin.post('/api/member', json={
            'name': name, 'username': name, 'password': 'review-password', 'role': 'manager'}).get_json()
        self.uid = created['id']
        self.admin.post(f'/api/admin/permissions/{self.uid}', json={'permissions': ['funds.review']})
        self.member = app_module.app.test_client()
        self.member.post('/login', json={'username': name, 'password': 'review-password'})

    def upload(self, client=None, content=None, filename='review.pdf'):
        reply = (client or self.member).post('/invoices/api/upload', data={
            'file': (io.BytesIO(content or b'%PDF-1.4\n' + uuid4().hex.encode()), filename)
        }, content_type='multipart/form-data')
        self.assertEqual(reply.status_code, 200, reply.get_data(as_text=True))
        return reply.get_json()['tmp_path']

    def confirm(self, tmp, **fields):
        return self.member.post('/invoices/api/upload/confirm', json={
            'tmp_path': tmp, 'month': '2026.10', 'amount': '1.00', **fields})

    def record(self):
        response = self.admin.post('/funds/api/records', json={
            'record_type': '进', 'amount': '5.00', 'record_date': '2026-10-13'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertIn('id', payload, payload)
        return payload

    def test_stale_review_rejected_and_unchanged_edit_keeps_version(self):
        record = self.record()
        rid = record['id']
        same = self.admin.put(f'/funds/api/records/{rid}',
                              json={'amount': '5.000', 'version': record['version']}).get_json()
        self.assertEqual(same['version'], record['version'])
        updated = self.admin.put(f'/funds/api/records/{rid}',
                                 json={'amount': '999.00', 'version': record['version']}).get_json()
        self.assertEqual(updated['version'], record['version'] + 1)
        stale = self.member.post(f'/funds/api/records/{rid}/review', json={'version': record['version']})
        self.assertEqual(stale.status_code, 409)
        with funds.get_funds_db() as db:
            row = db.execute('SELECT amount,review_status FROM records WHERE id=?', (rid,)).fetchone()
        db.close()
        self.assertEqual(tuple(row), ('999.00', '待审核'))
        current = self.member.post(f'/funds/api/records/{rid}/review', json={'version': updated['version']})
        self.assertEqual(current.get_json()['version'], updated['version'] + 1)

    def test_review_requires_valid_version_and_reports_missing_record(self):
        rid = self.record()['id']
        for value in (None, True, 1.5, 0, [], 'no', 2**63):
            reply = self.member.post(f'/funds/api/records/{rid}/review', json={'version': value})
            self.assertEqual(reply.status_code, 400)
        self.assertEqual(self.member.post(f'/funds/api/records/{rid}/review').status_code, 400)
        self.assertEqual(self.member.post('/funds/api/records/99999999/review', json={'version': 1}).status_code, 404)

    def test_two_reviewers_cannot_approve_same_version(self):
        record = self.record()
        outcomes = []
        barrier = threading.Barrier(2)
        def approve():
            client = app_module.app.test_client()
            with self.member.session_transaction() as original, client.session_transaction() as target:
                target.update(original)
            barrier.wait()
            outcomes.append(client.post(f"/funds/api/records/{record['id']}/review",
                                        json={'version': record['version']}).status_code)
        threads = [threading.Thread(target=approve) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(15)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(outcomes), [200, 409])

    def test_revision_migration_preserves_existing_table_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as root:
            db = sqlite3.connect(os.path.join(root, 'funds.db'))
            try:
                db.execute('CREATE TABLE records(id INTEGER PRIMARY KEY,amount_cents INTEGER,review_status TEXT)')
                db.execute("INSERT INTO records VALUES(7,123,'已审核')")
                db.commit()
                migrate_revisions(db); migrate_revisions(db)
                self.assertEqual(db.execute('SELECT * FROM records').fetchone(), (7, 123, '已审核', 1))
            finally:
                db.close()

    def test_invalid_invoice_fields_never_move_or_poison_stats(self):
        tmp = self.upload()
        for field, value in (('category', {'bad': 1}), ('notes', None), ('vendor', []),
                             ('invoice_number', 1), ('tags', [1]), ('date', '2026-02-30'),
                             ('month', '2026.13'), ('status', 'invalid')):
            with self.subTest(field=field):
                reply = self.confirm(tmp, **{field: value})
                self.assertEqual(reply.status_code, 400, reply.get_json())
                self.assertTrue(os.path.isfile(os.path.join(invoices.BASE_DIR, tmp)))
        good = self.confirm(tmp).get_json()['rel_path']
        prior = invoices.load_meta()[good].copy()
        for endpoint, body in (
            ('/invoices/api/invoice/update', {'id': good, 'category_override': {}}),
            ('/invoices/api/invoice/batch', {'ids': [good], 'updates': {'tags': 'wrong'}})):
            self.assertEqual(self.admin.post(endpoint, json=body).status_code, 400)
            self.assertEqual(invoices.load_meta()[good], prior)
        self.assertEqual(self.admin.get('/invoices/api/stats').status_code, 200)
        self.assertEqual(self.admin.get('/invoices/api/invoices?search=review').status_code, 200)
        self.admin.get('/invoices/api/export/download').close()

    def test_invalid_batch_item_retains_temp_while_valid_item_succeeds(self):
        bad, good = self.upload(), self.upload()
        reply = self.member.post('/invoices/api/upload/confirm/batch', json={'items': [
            {'tmp_path': bad, 'month': '2026.10', 'amount': '1', 'category': []},
            {'tmp_path': good, 'month': '2026.10', 'amount': '1'}]}).get_json()
        self.assertEqual((reply['ok'], reply['error']), (1, 1))
        self.assertTrue(os.path.isfile(os.path.join(invoices.BASE_DIR, bad)))
        self.assertFalse(os.path.exists(os.path.join(invoices.BASE_DIR, good)))

    def test_duplicate_confirmation_hides_other_members_paths(self):
        content = b'%PDF-1.4\n' + uuid4().hex.encode()
        foreign = self.upload(self.admin, content)
        self.admin.post('/invoices/api/upload/confirm', json={
            'tmp_path': foreign, 'month': '2026.10', 'amount': '1', 'submitter_id': 1})
        own = self.upload(content=content)
        reply = self.confirm(own).get_json()
        self.assertEqual(reply['code'], 'DUPLICATE')
        self.assertTrue(reply['duplicates'])
        self.assertTrue(all(entry['path'] == '' for entry in reply['duplicates']))

    def test_confirmed_date_vendor_used_by_single_batch_search_and_csv(self):
        date, vendor = '2026-10-14', '确认商家_' + uuid4().hex[:8]
        single = self.confirm(self.upload(), date=date, vendor=vendor).get_json()['rel_path']
        batch = self.member.post('/invoices/api/upload/confirm/batch', json={'items': [{
            'tmp_path': self.upload(), 'month': '2026.10', 'amount': '2.00',
            'date': date, 'vendor': vendor}]}).get_json()['results'][0]['path']
        for rel in (single, batch):
            row = self.member.get('/invoices/api/invoices', query_string={'id': rel}).get_json()['data'][0]
            self.assertEqual((row['date'], row['vendor']), (date, vendor))
            self.assertIn(vendor, rel)
        found = self.member.get('/invoices/api/invoices', query_string={'search': vendor}).get_json()['data']
        self.assertEqual({row['id'] for row in found}, {single, batch})
        download = self.admin.get('/invoices/api/export/download')
        self.assertEqual(download.status_code, 200, download.get_data(as_text=True))
        rows = list(csv.DictReader(io.StringIO(download.get_data(as_text=True).lstrip('\ufeff'))))
        download.close()
        selected = [row for row in rows if row['文件路径'] in (single, batch)]
        self.assertEqual(len(selected), 2, repr({'wanted': [single, batch],
                                              'exported_paths': [row['文件路径'] for row in rows]}))
        self.assertTrue(all((row['开票日期'], row['供应商']) == (date, vendor) for row in selected))

    def test_explicit_blank_date_vendor_override_filename_but_missing_fields_fall_back(self):
        filename = '类别-1元-原商家-2026.10.13-发票.pdf'
        blank = self.confirm(self.upload(filename=filename), date='', vendor='').get_json()['rel_path']
        legacy = self.confirm(self.upload(filename=filename)).get_json()['rel_path']
        blank_row = self.member.get('/invoices/api/invoices', query_string={'id': blank}).get_json()['data'][0]
        old_row = self.member.get('/invoices/api/invoices', query_string={'id': legacy}).get_json()['data'][0]
        self.assertEqual((blank_row['date'], blank_row['vendor']), ('', ''))
        self.assertEqual((old_row['date'], old_row['vendor']), ('2026-10-13', '原商家'))

    def test_nested_organize_preserves_metadata_cache_and_is_idempotent(self):
        with invoices._invoice_lock, tempfile.TemporaryDirectory() as root, patch.multiple(
            invoices, BASE_DIR=root, META_FILE=os.path.join(root, '.invoice_meta.json'),
            CACHE_FILE=os.path.join(root, '.invoice_cache.json'),
            SNAPSHOT_FILE=os.path.join(root, '.invoice_snapshot.json'),
            SNAPSHOT_STATE_FILE=os.path.join(root, '.invoice_snapshot_state.json')):
            meta = {}
            for sub in ('a', 'b'):
                rel = f'legacy/deep/{sub}/类别-1元-商家-2026.10.13-发票.pdf'
                full = os.path.join(root, rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'wb') as stream: stream.write(b'%PDF-1.4\n' + sub.encode())
                meta[rel] = {'rel_path': rel, 'filename': os.path.basename(rel), 'month': '2026.9',
                             'status': '已报销', 'submitter_id': self.uid, 'submitter': '历史显示名',
                             'notes': sub, 'confirmed_amount_cents': 9999,
                             'confirmed_vendor': '确认商家', 'confirmed_date': '2026-09-20',
                             'tags': ['验收'], 'invoice_number': 'fixture-' + sub,
                             'category_override': '确认类别', 'reimbursed_date': '2026-10-01'}
            invoices.save_meta(meta)
            invoices.save_json(invoices.CACHE_FILE, {key: {'amount_cents': 9999} for key in meta})
            preview = self.admin.get('/invoices/api/organize/preview').get_json()
            self.assertEqual(preview['count'], 2)
            self.assertTrue(all(row['status'] == '已报销' and row['month'] == '2026.9' for row in preview['files']))
            organized = self.admin.post('/invoices/api/organize', json={})
            self.assertEqual(organized.status_code, 200, organized.get_data(as_text=True))
            result = organized.get_json()['results']
            self.assertEqual((result['moved'], result['errors']), (2, 0))
            moved = invoices.load_meta()
            self.assertEqual(len(moved), 2)
            for entry in moved.values():
                original = next(value for value in meta.values() if value['notes'] == entry['notes'])
                for field in ('submitter_id', 'submitter', 'status', 'month', 'confirmed_amount_cents',
                              'tags', 'invoice_number', 'category_override', 'confirmed_vendor',
                              'confirmed_date', 'reimbursed_date'):
                    self.assertEqual(entry[field], original[field])
                self.assertTrue(os.path.isfile(os.path.join(root, entry['rel_path'])))
                self.assertNotIn('\\', entry['rel_path'])
            self.assertEqual(set(invoices.load_cache()), set(moved))
            repeated = self.admin.post('/invoices/api/organize', json={})
            self.assertEqual(repeated.status_code, 200, repeated.get_data(as_text=True))
            self.assertEqual(repeated.get_json()['results']['moved'], 0)
            self.assertEqual(invoices.load_meta(), moved)
            listing = self.member.get('/invoices/api/invoices')
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.headers['X-Invoice-Snapshot-State'], 'current')
            rows = listing.get_json()['data']
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row['amount'] == '99.99' for row in rows))

    def test_special_character_preview_and_ownership(self):
        rel = self.confirm(self.upload(), vendor="商家'% +", date='2026-10-14').get_json()['rel_path']
        response = self.member.get('/invoices/api/open_file', query_string={'rel_path': rel})
        self.assertEqual(response.status_code, 200)
        self.member.get(response.get_json()['url']).close()
        name = 'other_' + uuid4().hex[:8]
        self.admin.post('/api/member', json={'name': name, 'username': name, 'password': 'review-password'})
        other = app_module.app.test_client()
        other.post('/login', json={'username': name, 'password': 'review-password'})
        self.assertEqual(other.get('/invoices/api/open_file', query_string={'rel_path': rel}).status_code, 403)

    def test_seed_uses_current_schema_is_idempotent_and_requires_first_password_change(self):
        from deploy import seed_data
        from common import DB_PATH, get_db
        name = 'seed_' + uuid4().hex[:8]
        with tempfile.TemporaryDirectory() as root, patch.object(seed_data, 'DB_PATH', DB_PATH), \
                contextlib.redirect_stdout(io.StringIO()):
            seed_path = os.path.join(root, 'seed-import.json')
            with open(seed_path, 'w', encoding='utf-8') as stream:
                json.dump({'members': [(name, name, '视觉;电控', 'member')],
                           'tasks': [(name, name, '哨兵', '视觉', '未开始', 80, '中', '')]},
                          stream, ensure_ascii=False)
            seed_data.main(seed_path)
            with app_module.app.app_context():
                db = get_db()
                before = dict(db.execute('SELECT * FROM members WHERE username=?', (name,)).fetchone())
            seed_data.main(seed_path)
        with app_module.app.app_context():
            db = get_db()
            after = dict(db.execute('SELECT * FROM members WHERE username=?', (name,)).fetchone())
            self.assertEqual(before, after)
            self.assertEqual(after['must_change_password'], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM member_groups WHERE member_id=?', (after['id'],)).fetchone()[0], 2)
            task = db.execute('SELECT id,version,project_id FROM tasks WHERE title=?', (name,)).fetchone()
            self.assertEqual(task['version'], 1)
            self.assertIsNotNone(task['project_id'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM tasks WHERE title=?', (name,)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT kind FROM task_revisions WHERE task_id=?', (task['id'],)).fetchone()[0], 'create')
            self.assertTrue(db.execute('SELECT 1 FROM member_projects WHERE member_id=? AND project_id=?',
                                       (after['id'], task['project_id'])).fetchone())

    def test_seed_import_is_atomic_when_later_task_is_invalid(self):
        from deploy import seed_data
        from common import DB_PATH, get_db
        unique = 'atomic_' + uuid4().hex[:10]
        with tempfile.TemporaryDirectory() as root, patch.object(seed_data, 'DB_PATH', DB_PATH), \
                contextlib.redirect_stdout(io.StringIO()):
            seed_path = os.path.join(root, 'seed-import.json')
            payload = {
                'members': [(unique, unique, '视觉', 'member')],
                'tasks': [
                    (unique + ' good', unique, '哨兵', '视觉', '未开始', 0, '中', ''),
                    (unique + ' invalid', unique, '哨兵', '视觉', '非法状态', 0, '中', ''),
                ],
            }
            with open(seed_path, 'w', encoding='utf-8') as stream:
                json.dump(payload, stream, ensure_ascii=False)
            with self.assertRaises(RuntimeError):
                seed_data.main(seed_path)
        with app_module.app.app_context():
            db = get_db()
            self.assertIsNone(db.execute('SELECT id FROM members WHERE username=?', (unique,)).fetchone())
            self.assertEqual(db.execute('SELECT count(*) FROM tasks WHERE title LIKE ?',
                                        (unique + '%',)).fetchone()[0], 0)

    def test_seed_missing_local_file_does_not_open_database(self):
        from deploy import seed_data
        self.assertEqual(seed_data.MEMBERS, [])
        self.assertEqual(seed_data.TASKS, [])
        with tempfile.TemporaryDirectory() as root, patch.object(seed_data.sqlite3, 'connect') as connect:
            with self.assertRaises(FileNotFoundError):
                seed_data.main(os.path.join(root, 'missing.json'))
            connect.assert_not_called()

    def test_seed_refuses_archived_catalog_and_missing_admin_without_writes(self):
        from deploy import seed_data
        from common import DB_PATH
        name = 'seed_' + uuid4().hex[:8]
        project = self.admin.post('/api/projects', json={'name': name}).get_json()['id']
        self.admin.post(f'/api/projects/{project}/delete', json={'confirm_name': name})
        with patch.multiple(seed_data, DB_PATH=DB_PATH, MEMBERS=[(name, name, '视觉', 'member')],
                            TASKS=[(name, name, name, '视觉', '未开始', 0, '中', '')]):
            with self.assertRaisesRegex(RuntimeError, '已删除'):
                seed_data.main()
        with contextlib.closing(sqlite3.connect(DB_PATH)) as db:
            self.assertIsNone(db.execute('SELECT id FROM members WHERE username=?', (name,)).fetchone())
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, 'no-admin.db')
            with contextlib.closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE members(id INTEGER,name TEXT,role TEXT,is_active INTEGER,deleted_at TEXT,must_change_password INTEGER)')
                db.commit()
            with patch.object(seed_data, 'DB_PATH', path), self.assertRaisesRegex(RuntimeError, '管理员'):
                seed_data.main()

    def test_legacy_diagnostic_is_read_only_and_reports_only_evidence(self):
        from deploy.audit_review_data import audit
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / 'data/invoices'
            directory.mkdir(parents=True)
            path = Path(root) / 'data.db'
            with contextlib.closing(sqlite3.connect(path)) as db:
                db.executescript("CREATE TABLE members(id INTEGER,group_name TEXT);"
                                 "CREATE TABLE member_groups(member_id INTEGER,group_id INTEGER);"
                                 "CREATE TABLE tasks(id INTEGER,project TEXT,project_id INTEGER);"
                                 "INSERT INTO members VALUES(1,'视觉');INSERT INTO tasks VALUES(2,'哨兵',NULL);")
            meta = directory / '.invoice_meta.json'
            meta.write_text('{"missing.pdf":{"category_override":[],"confirmed_amount_cents":"1"}}', encoding='utf-8')
            files = (path, meta)
            before = [hashlib.sha256(item.read_bytes()).hexdigest() for item in files]
            report = audit(root, db_path=path, invoice_dir=directory)
            self.assertTrue(report['read_only'])
            self.assertEqual({issue['kind'] for issue in report['issues']}, {
                'missing_member_groups', 'missing_task_project', 'orphan_invoice_metadata',
                'invalid_invoice_fields', 'invalid_confirmed_amount'})
            self.assertEqual(before, [hashlib.sha256(item.read_bytes()).hexdigest() for item in files])


    def test_funds_delete_requires_current_version_and_audits_the_record(self):
        record = self.record()
        rid = record['id']
        self.assertEqual(self.admin.delete(f'/funds/api/records/{rid}').status_code, 400)
        self.assertEqual(
            self.admin.delete(f'/funds/api/records/{rid}',
                              query_string={'version': record['version'] + 5}).status_code, 409)
        with funds.get_funds_db() as db:
            still_there = db.execute('SELECT COUNT(*) FROM records WHERE id=?', (rid,)).fetchone()[0]
        db.close()
        self.assertEqual(still_there, 1)
        removed = self.admin.delete(f'/funds/api/records/{rid}',
                                    query_string={'version': record['version']})
        self.assertEqual(removed.status_code, 200, removed.get_json())
        with funds.get_funds_db() as db:
            gone = db.execute('SELECT COUNT(*) FROM records WHERE id=?', (rid,)).fetchone()[0]
        db.close()
        self.assertEqual(gone, 0)
        from common import get_db
        with app_module.app.app_context():
            audit = get_db().execute(
                "SELECT details FROM security_audit WHERE action='funds.delete_record' AND target=? "
                "ORDER BY id DESC LIMIT 1", (f'/funds/api/records/{rid}',)).fetchone()
        self.assertIsNotNone(audit, 'deleting a record must leave a durable audit row')
        self.assertIn('"deleted"', audit['details'])
        self.assertIn(f'"id": {rid}', audit['details'])

    def test_only_a_reviewer_may_delete_an_approved_record(self):
        record = self.record()
        rid = record['id']
        self.assertTrue(self.member.post(
            f'/funds/api/records/{rid}/review', json={'version': record['version']}).get_json()['code'] == 0)
        plain_name = 'plain_' + uuid4().hex[:8]
        created = self.admin.post('/api/member', json={
            'name': plain_name, 'username': plain_name, 'password': 'plain-password', 'role': 'manager'})
        self.assertTrue(created.get_json()['ok'])
        plain = app_module.app.test_client()
        self.assertTrue(plain.post('/login', json={
            'username': plain_name, 'password': 'plain-password'}).get_json()['ok'])
        denied = plain.delete(f'/funds/api/records/{rid}', query_string={'version': record['version'] + 1})
        self.assertEqual(denied.status_code, 403, denied.get_json())
        allowed = self.member.delete(f'/funds/api/records/{rid}',
                                     query_string={'version': record['version'] + 1})
        self.assertEqual(allowed.status_code, 200, allowed.get_json())

    def test_duplicate_invoice_cannot_be_forced_by_a_non_finance_member(self):
        content = b'%PDF-1.4\n' + uuid4().hex.encode()
        tmp = self.upload(content=content)
        self.assertTrue(self.confirm(tmp).get_json()['success'])
        duplicate_tmp = self.upload(content=content)
        blocked = self.confirm(duplicate_tmp)
        self.assertEqual(blocked.get_json().get('code'), 'DUPLICATE')
        forced = self.confirm(duplicate_tmp, allow_duplicate=True)
        self.assertEqual(forced.status_code, 403, forced.get_json())
        # Finance may force its own duplicate, never another member's temp file.
        admin_tmp = self.upload(client=self.admin, content=content)
        blocked_finance = self.admin.post('/invoices/api/upload/confirm', json={
            'tmp_path': admin_tmp, 'month': '2026.10', 'amount': '1.00'})
        self.assertEqual(blocked_finance.get_json().get('code'), 'DUPLICATE')
        allowed = self.admin.post('/invoices/api/upload/confirm', json={
            'tmp_path': admin_tmp, 'month': '2026.10', 'amount': '1.00',
            'allow_duplicate': True})
        self.assertTrue(allowed.get_json()['success'], allowed.get_json())

    def test_single_record_endpoint_returns_money_fields_and_404s(self):
        record = self.record()
        rid = record['id']
        response = self.admin.get(f'/funds/api/records/{rid}')
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()['data']
        self.assertEqual(data['id'], rid)
        self.assertEqual(data['amount'], '5.00')
        self.assertEqual(data['amount_cents'], '500')
        self.assertEqual(data['version'], record['version'])
        self.assertEqual(self.admin.get('/funds/api/records/99999999').status_code, 404)
