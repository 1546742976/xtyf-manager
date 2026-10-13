"""Regressions for the defects reproduced during the whole-project review."""
import io
import os
import sqlite3
import tempfile
import threading
import unittest
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
        return self.admin.post('/funds/api/records', json={
            'record_type': '进', 'amount': '5.00', 'record_date': '2026-10-13'}).get_json()

    def test_stale_review_rejected_and_unchanged_edit_keeps_version(self):
        record = self.record()
        rid = record['id']
        same = self.admin.put(f'/funds/api/records/{rid}', json={'amount': '5.000'}).get_json()
        self.assertEqual(same['version'], record['version'])
        updated = self.admin.put(f'/funds/api/records/{rid}', json={'amount': '999.00'}).get_json()
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
